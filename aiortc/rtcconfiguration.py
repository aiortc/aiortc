import attr

@attr.s
class RTCConfiguration:
    bundlePolicy = attr.ib(default='max-compat')
