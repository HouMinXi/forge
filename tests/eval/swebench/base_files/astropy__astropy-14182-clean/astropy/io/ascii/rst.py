



























class SimpleRSTData(FixedWidthData):
    start_line = 3
    end_line = -1
    splitter_class = FixedWidthTwoLineDataSplitter







    Example::

        ==== ===== ======
        Col1  Col2  Col3
        ==== ===== ======
          1    2.3  Hello
          2    4.5  Worlds
        ==== ===== ======

    Currently there is no support for reading tables which utilize continuation lines,
    or for ones which define column spans through the use of an additional






    data_class = SimpleRSTData
    header_class = SimpleRSTHeader

    def __init__(self):
        super().__init__(delimiter_pad=None, bookend=False)

    def write(self, lines):
        lines = super().write(lines)
        lines = [lines[1]] + lines + [lines[1]]
        return lines
