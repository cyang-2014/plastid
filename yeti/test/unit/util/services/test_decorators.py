#!/usr/bin/env python
import sys
import os
import fcntl
import types
import warnings
from nose.plugins.attrib import attr
from nose.tools import assert_equal, assert_true, assert_greater_equal, assert_raises, assert_set_equal, assert_not_equal
from yeti.util.services.decorators import notimplemented, \
                                                 NotImplementedException, \
                                                 deprecated, \
                                                 parallelize, \
                                                 in_separate_process, \
                                                 catch_stderr, \
                                                 catch_stdout, \
                                                 catch_warnings

#===============================================================================
# INDEX: functions and classes that will be decorated
#===============================================================================

def stderr_func(msg):
    sys.stderr.write(msg)
    return msg

def stdout_func(msg):
    sys.stdout.write(msg)
    return msg

def util_func(x):
    """Square numbers and return process in which function was run
    
    Parameters
    ----------
    x : int or float

    Returns
    -------
    int or float
        Squared value of ``x``
    
    int
        Process ID in which function was run
    """
    return x**2, os.getpid()

def get_pipes():
    readfd, writefd = os.pipe()
    fcntl.fcntl(readfd,fcntl.F_SETFL,os.O_NONBLOCK)
    return os.fdopen(readfd), os.fdopen(writefd,"w")

class UtilClass(object):
    def __init__(self,tmp):
        self.name = tmp
    
    def get_name(self):
        return self.name

#===============================================================================
# INDEX: unit tests
#===============================================================================

# stdout/err redirection -------------------------------------------------------

@attr(test="unit")
def test_catch_stderr_doesnt_print_without_buffer():
    # spy on `inner` by making sure there is nothing written to stderr
    outer_reader, outer_writer = get_pipes()
    message = "this is a test"
    
    @catch_stderr(outer_writer)
    def inner():
        wrapped = catch_stderr()(stderr_func)
        # make sure value is returned from wrapped function
        msg = wrapped(message)
        assert_equal(msg,message)
    
    inner()

    # make sure no message made it out of `inner`
    # this means using non-blocking IO and having an IOError
    # because resource isn't ready
    outer_writer.flush()
    outer_writer.close()
    assert_equal("",outer_reader.read())
    outer_reader.close()
    
@attr(test="unit")
def test_catch_stderr_doesnt_print_with_buffer_but_catches_in_buffer():
    # spy on `inner` by making sure there is nothing written to stderr
    # but make sure message is found in inner readre
    outer_reader, outer_writer = get_pipes()
    message = "this is a test"
    
    @catch_stderr(outer_writer)
    def inner():
        inner_reader, inner_writer = get_pipes()
        wrapped = catch_stderr(inner_writer)(stderr_func)
        
        # make sure value is returned from wrapped function
        msg = wrapped(message)
        assert_equal(message,msg)
        
        # make sure we caught entire message from stderr
        inner_writer.flush()
        inner_writer.close()
        assert_equal(message,inner_reader.read())
        inner_reader.close()
    
    inner()
    # make sure no message made it out of `inner`
    # this means using non-blocking IO and having an IOError
    # because resource isn't ready
    outer_writer.flush()
    outer_writer.close()
    assert_equal("",outer_reader.read())
    outer_reader.close()


# catch warnings    ------------------------------------------------------------

@attr(test="unit")
def test_catch_warnings_catches_warnings():
    dep_func = deprecated(util_func)
    ign_func = catch_warnings("ignore")(dep_func)

    num = 5
    with warnings.catch_warnings(record=True) as warns:
        warnings.simplefilter("always")
        for x in range(num):
            assert_equal(dep_func(x),util_func(x))
            dep_func(x)
    
    # make sure warning is issued with deprecated
    assert_greater_equal(len(warns),num)

    num = 5
    with warnings.catch_warnings(record=True) as warns:
        warnings.simplefilter("always")
        for x in range(num):
            assert_equal(ign_func(x),util_func(x))
            ign_func(x)
    
    # make sure warning is caught with wrapped function
    assert_greater_equal(len(warns),0)


# notimplemented    ------------------------------------------------------------

@attr(test="unit")
def test_notimplemented_raises_exception():
    my_func = notimplemented(util_func)
    assert_true(isinstance(my_func,types.FunctionType))
    
    assert_raises(NotImplementedException,my_func,5)

# deprecated    ----------------------------------------------------------------

@attr(test="unit")
def test_deprecated_function_raises_warning():
    num = 5
    my_func = deprecated(util_func)
    assert_true(isinstance(my_func,types.FunctionType))
    
    with warnings.catch_warnings(record=True) as warns:
        warnings.simplefilter("always")
        for x in range(num):
            assert_equal(my_func(x),util_func(x))
            my_func(x)
    
    # make sure warning is issued
    assert_greater_equal(len(warns),num)

@attr(test="unit")
def test_deprecated_class_raises_warning():
    reg_obj = UtilClass("my_object")
    dep_class = deprecated(UtilClass)

    with warnings.catch_warnings(record=True) as warns:
        warnings.simplefilter("always")
        dep_obj = dep_class("my_object")
        assert_true(isinstance(dep_obj,UtilClass))
    
    # make sure warning is issued
    assert_equal(len(warns),1)
    
    # make sure wrapped class behaves as it should
    assert_equal(reg_obj.get_name(),dep_obj.get_name())


# parallelize or in other processes --------------------------------------------

@attr(test="unit")
def test_parallelize_spawns_processes_and_gets_correct_ansswer():
    x = range(500)
    my_func = parallelize(util_func)
    outer_vals, outer_pids = zip(*[util_func(X) for X in x])
    inner_vals, inner_pids = zip(*my_func(x))

    assert_set_equal(set(outer_vals),set(inner_vals))
    assert_not_equal(set(outer_pids),set(inner_pids))

@attr(test="unit")
def test_in_separate_process_spawns_process_and_gets_correct_ansswers():
    my_func = in_separate_process(util_func)
    assert_true(isinstance(my_func,types.FunctionType))
    
    for x in range(100):
        res_out, outer_pid = my_func(x)
        res_in,  inner_pid = util_func(x)        
        assert_equal(res_out,res_in)
        assert_not_equal(outer_pid,inner_pid)
        
