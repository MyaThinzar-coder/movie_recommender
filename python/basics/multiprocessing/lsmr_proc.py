import multiprocessing, numpy

_cpu_count = multiprocessing.cpu_count()

_processes = [] # child processes
_pipes = [] # pipes to child processes

_process_data = {} # data to survive in between function calls



def _message_loop(conn):
    # conn is a multiprocessing.connection.PipeConnection

    while True:
        request = conn.recv()
        op = request["op"]

        if op == "shutdown":
            return

        elif op == "set_data":
            for key in request:
                if key != "op":
                    _process_data[key] = request[key]

        elif op == "get_var":
            var_name = request["var_name"]
            conn.send(_process_data[var_name])

        elif op == "clear_all_data":
            _process_data.clear()

        elif op == "run_function":
            op_name = request["op_name"]

            if op_name in globals():
                globals()[op_name](request)
            else:
                print("Unhandled operation:", op)
                print("op_name:", op_name)
                # print("Request =", request) - request might be too large
        else:
            print("Unhandled operation:", op)
            # print("Request =", request) - request might be too large


def start_processes():
    """Spawns (cpu_count - 1) processes and pipes."""
    for i in range(0, _cpu_count - 1):
        pipe1, pipe2 = multiprocessing.Pipe()
        process = multiprocessing.Process(target = _message_loop,
                                          args=(pipe2,))
        process.start()
        _processes.append(process)
        _pipes.append(pipe1)


def end_processes():
    """Send "shutdown" message to all processes and wait for
    them to terminate."""
    for pipe in _pipes:
        pipe.send({"op": "shutdown"})

    for process in _processes:
        process.join()


def send_same_data(data_dict):
    """Send same data to all processes. The data_dict
    is the request object itself, so the "op" key
    cannot be used."""

    # for the local process:
    for key in data_dict:
        _process_data[key] = data_dict[key]

    data_dict["op"] = "set_data"

    for pipe in _pipes:
        pipe.send(data_dict)


def define_list(length):
    """ Sets up a list definition structure that
    represents a single list spread across different
    processors.
    ::
        Output:
            _process_data["list_def"] = (list_start, num_rows), where
                "list_start" is unique for each processor.
    """
    indices = _split(0, length, _cpu_count)
    for i in range(0, len(_pipes)):
        _pipes[i].send({
            "op": "set_data",
            "list_def": indices[i]
        })

    _process_data["list_def"] = indices[-1]


def split_list_and_send(data_list, var_name : str):
    """Split "data_list" and send it to each process. The
    current process gets the final split."""
    # compute split
    indices_and_lengths = _split(0, len(data_list), _cpu_count)

    # send to other processes
    for i in range(0, len(_pipes)):
        index = indices_and_lengths[i][0]
        length = indices_and_lengths[i][1]

        if length > 0:
            _pipes[i].send({
                "op": "set_data",
                var_name: data_list[index : index + length]
            })

    # the current process gets the final split
    index = indices_and_lengths[-1][0]
    _process_data[var_name] = data_list[index:]


def clear_all_data():
    for pipe in _pipes:
        pipe.send({"op": "clear_all_data"})

    _process_data.clear()


def run_function(function_name : str, arg_dict):
    """Run "function_name", with arg_dict being the
    argument dictionary."""
    if arg_dict is None:
        arg_dict = {}

    # The keys "op" and "op_name" will be over written - so these
    # should not be present in "arg_dict".
    if ("op" in arg_dict) or ("op_name" in arg_dict):
        raise Exception("run_function() cannot accept arg_dict with "
                        + 'keys "op" or "op_name".')

    # arg_dict is used as the request
    arg_dict["op"] = "run_function"
    arg_dict["op_name"] = function_name

    for pipe in _pipes:
        pipe.send(arg_dict)

    # for local process
    globals()[function_name](arg_dict)


def concat_var_into_list(var_name : str):
    """Merge "var_name" in all processes and
    concatenate them into a single list."""
    for pipe in _pipes:
        pipe.send({
            "op": "get_var",
            "var_name": var_name
        })

    var_list = []
    for pipe in _pipes:
        var_list += pipe.recv()

    var_list += _process_data[var_name]
    return var_list


def concat_var_into_numpy_array(var_name : str):
    """Retrieve "var_name" from all processes
    as numpy arrays, and use numpy.concatenate()
    to merge them into a single numpy array."""
    for pipe in _pipes:
        pipe.send({
            "op": "get_var",
            "var_name": var_name
        })

    var_array = None
    for pipe in _pipes:
        if var_array is None:
            var_array = pipe.recv()
        else:
            var_array = numpy.concatenate((var_array, pipe.recv()))

    var_array = numpy.concatenate((var_array, _process_data[var_name]))
    return var_array


def _split(start: int, length: int, num_splits: int):
    """Given a "start" and a "length", generate
    a list of (index, length) pairs. For example,
    (start=10, length=8, num_splits=4) generates
    [(10, 2), (12, 2), (14, 2), (16, 2)]."""

    if length >= num_splits:
        # standard case
        # compute the indices
        indices = []
        for i in range(0, num_splits):
            indices.append(start + int(length * i / num_splits))

        result = []
        # most of the lengths are (next index - current index)
        for i in range(0, len(indices) - 1):
            result.append((indices[i], indices[i+1] - indices[i]))

        # the length for the final index:
        final_length = start + length - indices[-1]
        result.append((indices[-1], final_length))

        return result

    else:
        # special case
        result = []
        index = start
        for i in range(0, num_splits):
            if index < start + length:
                result.append((index, 1))
                index += 1
            else:
                result.append((index, 0))

        return result


# End of framework
#####################################################################


def _create_data(request):
    """ Creates "row" and "col" numpy arrays.
    ::
        Input:
            _process_data["list_def"] = (list_start, num_rows)
            _process_data["block_length"], ["non_zeros_per_row"]
        Output:
            _process_data["row"], ["col"]
    """
    list_start = _process_data["list_def"][0]
    num_rows = _process_data["list_def"][1]
    block_length = _process_data["block_length"]
    non_zeros_per_row = _process_data["non_zeros_per_row"]

    length = num_rows * non_zeros_per_row
    row = numpy.zeros(length)

    # the "col" gets a random index between 0 and "block_length"
    # an offset will be applied later
    col = numpy.random.random_integers(0, block_length - 1, length)

    index = 0

    # the following nested for loop takes a long time
    for i in range(0, num_rows):
        for j in range(0, non_zeros_per_row):
            row[index] = list_start + i

            # apply offset to the random number already populated in "col"
            col[index] += j * block_length

            index += 1

    _process_data["row"] = row
    _process_data["col"] = col


