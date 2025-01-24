import time
import pytest
from pyarallel import parallel, RateLimit

def test_basic_parallel():
    @parallel(max_workers=2)
    def square(x):
        return x * x
    
    results = square([1, 2, 3, 4])
    assert sorted(results) == [1, 4, 9, 16]

def test_single_item():
    @parallel()
    def double(x):
        return x * 2
    
    result = double(5)
    assert result == [10]

def test_rate_limiting():
    @parallel(rate_limit=(2, "second"))
    def slow_op(x):
        return x
    
    start = time.time()
    results = slow_op([1, 2, 3, 4])
    duration = time.time() - start
    
    assert sorted(results) == [1, 2, 3, 4]
    assert duration >= 1.5  # Should take at least 1.5s for 4 items at 2/sec

def test_batch_processing():
    processed = []
    
    @parallel(batch_size=2)
    def batch_op(x):
        processed.append(x)
        return x
    
    results = batch_op([1, 2, 3, 4])
    
    # Should process in 2 batches
    assert len(processed) == 4
    assert sorted(results) == [1, 2, 3, 4]

# Helper function for process pool test
def _cpu_bound(x):
    return x * x

cpu_bound = parallel(executor_type="process")(_cpu_bound)

def test_process_pool():
    results = cpu_bound([1, 2, 3])
    assert sorted(results) == [1, 4, 9]

def test_error_handling():
    @parallel()
    def failing_func(x):
        if x == 2:
            raise ValueError("Bad value")
        return x
    
    with pytest.raises(ValueError):
        failing_func([1, 2, 3])

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
