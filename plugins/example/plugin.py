"""Reference plugin used by lifecycle tests and local development."""


def on_enable(config):
    return {"status": "enabled"}


def on_disable(config):
    return {"status": "disabled"}


def on_configure(config):
    return {"status": "configured"}
