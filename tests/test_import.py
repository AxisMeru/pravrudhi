def test_engine_imports_kernel() -> None:
    import pravrudhi
    import pravrudhi_kernel

    # Asserted as a property, not a value. This line read `== "0.4.0"` while pyproject said 0.4.2 and tag
    # v0.4.2 was cut, so the test was pinning the drift in place rather than catching it: bumping the release
    # would have turned it red, which is a standing reason not to bump. What matters is that a version is
    # reported at all and that it is the one this build was made from.
    assert pravrudhi.__version__ and pravrudhi.__version__ != "0+unknown"
    assert pravrudhi_kernel.__version__ == pravrudhi.KERNEL_VERSION
