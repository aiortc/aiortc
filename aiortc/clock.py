import datetime

NTP_EPOCH = datetime.datetime(1900, 1, 1, tzinfo=datetime.timezone.utc)


def current_datetime():
    return datetime.datetime.now(datetime.timezone.utc)


def current_ms():
    delta = current_datetime() - NTP_EPOCH
    return int(delta.total_seconds() * 1000)


def current_ntp_time():
    return datetime_to_ntp(current_datetime())


def datetime_from_ntp(ntp):
    seconds = (ntp >> 32)
    microseconds = ((ntp & 0xffffffff) * 1000000) / (1 << 32)
    return NTP_EPOCH + datetime.timedelta(seconds=seconds, microseconds=microseconds)


def datetime_to_ntp(dt):
    delta = dt - NTP_EPOCH
    high = int(delta.total_seconds())
    low = round((delta.microseconds * (1 << 32)) // 1000000)
    return (high << 32) | low
