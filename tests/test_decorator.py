"""Tests for the @parallel decorator."""

from pyarallel import parallel, parallel_map


@parallel(workers=2, executor="process")
def _process_double(x):
    return x * 2


@parallel(workers=2, executor="process")
def _process_add(a, b):
    return a + b


class TestBasicDecorator:
    def test_single_call_returns_plain_value(self):
        @parallel(workers=2)
        def double(x):
            return x * 2

        assert double(5) == 10  # Returns int, NOT [int]

    def test_bare_decorator_no_parens(self):
        @parallel
        def double(x):
            return x * 2

        assert double(5) == 10

    def test_map_returns_parallel_result(self):
        @parallel(workers=2)
        def double(x):
            return x * 2

        results = double.map([1, 2, 3])
        assert list(results) == [2, 4, 6]

    def test_map_preserves_order(self):
        import time

        @parallel(workers=4)
        def slow_first(x):
            if x == 0:
                time.sleep(0.05)
            return x

        assert list(slow_first.map(range(5))) == [0, 1, 2, 3, 4]


class TestDecoratorOverrides:
    def test_override_workers(self):
        @parallel(workers=1)
        def identity(x):
            return x

        results = identity.map([1, 2, 3], workers=4)
        assert list(results) == [1, 2, 3]

    def test_override_rate_limit(self):
        import time

        @parallel(workers=4)
        def identity(x):
            return x

        start = time.monotonic()
        identity.map(range(5), rate_limit=10)
        elapsed = time.monotonic() - start
        assert elapsed >= 0.3

    def test_process_executor_map(self):
        results = _process_double.map([1, 2, 3])
        assert list(results) == [2, 4, 6]

    def test_process_executor_starmap(self):
        results = _process_add.starmap([(1, 2), (3, 4)])
        assert list(results) == [3, 7]

    def test_process_executor_stream(self):
        results = list(_process_double.stream([1, 2, 3]))
        results.sort(key=lambda item: item.index)
        assert [(item.index, item.value) for item in results] == [
            (0, 2),
            (1, 4),
            (2, 6),
        ]


class TestMethodSupport:
    def test_instance_method_single_call(self):
        class Processor:
            def __init__(self, factor):
                self.factor = factor

            @parallel(workers=2)
            def multiply(self, x):
                return x * self.factor

        p = Processor(3)
        assert p.multiply(5) == 15

    def test_instance_method_map(self):
        class Processor:
            def __init__(self, factor):
                self.factor = factor

            @parallel(workers=2)
            def multiply(self, x):
                return x * self.factor

        p = Processor(3)
        results = p.multiply.map([1, 2, 3])
        assert list(results) == [3, 6, 9]

    def test_different_instances(self):
        class Adder:
            def __init__(self, n):
                self.n = n

            @parallel(workers=2)
            def add(self, x):
                return x + self.n

        a = Adder(10)
        b = Adder(100)
        assert list(a.add.map([1, 2])) == [11, 12]
        assert list(b.add.map([1, 2])) == [101, 102]

    def test_static_method(self):
        class Math:
            @staticmethod
            @parallel(workers=2)
            def square(x):
                return x**2

        assert Math.square(3) == 9
        assert list(Math.square.map([1, 2, 3])) == [1, 4, 9]

    def test_parallel_map_with_bound_method(self):
        """parallel_map works directly with bound methods."""

        class Doubler:
            @parallel(workers=2)
            def go(self, x):
                return x * 2

        d = Doubler()
        results = parallel_map(d.go, [1, 2, 3], workers=2)
        assert list(results) == [2, 4, 6]


class TestPreservedSignature:
    def test_wraps_preserves_name(self):
        @parallel(workers=2)
        def my_function(x):
            """My docstring."""
            return x

        assert my_function.__name__ == "my_function"
        assert my_function.__doc__ == "My docstring."

    def test_wrapped_attribute(self):
        @parallel
        def f(x):
            return x

        assert hasattr(f, "__wrapped__")
