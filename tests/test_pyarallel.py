import time
import pytest
from pyarallel import parallel, RateLimit

def test_basic_parallel(parallel_function):
    results = parallel_function([1, 2, 3, 4])
    assert sorted(results) == [1, 4, 9, 16]

@pytest.mark.parametrize("input_value,expected", [
    (5, [10]),
    (0, [0]),
    (-1, [-2])
])
def test_single_item(input_value, expected):
    @parallel()
    def double(x):
        return x * 2
    
    result = double(input_value)
    assert result == expected

def test_rate_limiting(rate_limit):
    @parallel(rate_limit=rate_limit)
    def slow_op(x):
        return x
    
    start = time.time()
    results = slow_op([1, 2, 3, 4])
    duration = time.time() - start
    
    assert sorted(results) == [1, 2, 3, 4]
    assert duration >= 1.5  # Should take at least 1.5s for 4 items at 2/sec

def test_batch_processing(batch_processor):
    results = batch_processor([1, 2, 3, 4])
    
    # Should process in 2 batches
    assert len(batch_processor.processed) == 4
    assert sorted(results) == [1, 2, 3, 4]

def test_process_pool(process_pool_function):
    results = process_pool_function([1, 2, 3])
    assert sorted(results) == [1, 4, 9]

@pytest.mark.parametrize("error_value,error_type,error_msg", [
    (2, ValueError, "Bad value"),
    (None, TypeError, "unsupported operand type\(s\) for \*: 'NoneType' and 'NoneType'")
])
def test_error_handling(error_value, error_type, error_msg):
    @parallel()
    def failing_func(x):
        if x == 2:
            raise ValueError("Bad value")
        return x * x
    
    with pytest.raises(error_type, match=error_msg):
        failing_func([1, error_value, 3])

def test_prewarm():
    @parallel(max_workers=2, prewarm=True)
    def quick_op(x):
        return x
    
    # First call should be fast since workers are prewarmed
    start = time.time()
    result = quick_op([1])
    duration = time.time() - start
    
    assert result == [1]
    assert duration < 0.1  # Should be very quick

@pytest.mark.parametrize("worker_count,input_size", [
    (2, 4),
    (4, 8),
    (1, 2)
])
def test_worker_scaling(worker_count, input_size):
    @parallel(max_workers=worker_count)
    def scale_test(x):
        return x * 2
    
    inputs = list(range(input_size))
    results = scale_test(inputs)
    assert len(results) == input_size
    assert sorted(results) == [x * 2 for x in inputs]

def test_rate_limit_object():
    rate = RateLimit(2, "second")
    
    @parallel(rate_limit=rate)
    def rate_limited(x):
        return x
    
    start = time.time()
    results = rate_limited([1, 2, 3, 4])
    duration = time.time() - start
    
    assert sorted(results) == [1, 2, 3, 4]
    assert duration >= 1.5
