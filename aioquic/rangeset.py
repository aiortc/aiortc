class RangeSet:
    def __init__(self):
        self.ranges = []

    def add(self, pn):
        for i, r in enumerate(self.ranges):
            if pn in r:
                return
            if r.stop == pn:
                if i < len(self.ranges) - 1 and self.ranges[i + 1].start == pn + 1:
                    self.ranges[i] = range(r.start, self.ranges[i + 1].stop)
                    self.ranges.pop(i + 1)
                else:
                    self.ranges[i] = range(r.start, r.stop + 1)
                return
            if r.start == pn + 1:
                self.ranges[i] = range(pn, r.stop)
                return

        self.ranges.append(range(pn, pn + 1))

    def __bool__(self):
        return bool(self.ranges)
