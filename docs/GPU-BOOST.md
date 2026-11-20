# Optional GPU boost

The GPU boost daemon can hold a system-wide request for maximum GPU clocks.
Install it from the verified driver package with `sudo env NULLMOTH_GPU_BOOST=1 ./install.sh`.
Continuous boost can increase power use, temperature and fan speed.

From `pkgroot/`, use `sudo ./Optional/GPU-Boost/boost_daemon.sh set off` to
release the request. Use `set on` to enable it again or `remove` to unload
the daemon and remove its files. The optional install respects an existing
boost setting.
