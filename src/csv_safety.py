"""Excel-safe exports. Keep raw machine data available explicitly where needed."""
import csv


def spreadsheet_cell(value):
    if not isinstance(value, str):
        return value
    if value.startswith(("\t", "\r", "\n")) or value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


class SafeCSVWriter:
    def __init__(self, stream, **kwargs):
        self.writer = csv.writer(stream, **kwargs)

    def writerow(self, row):
        return self.writer.writerow([spreadsheet_cell(value) for value in row])

    def writerows(self, rows):
        for row in rows:
            self.writerow(row)


class SafeDictWriter(csv.DictWriter):
    def writerow(self, rowdict):
        return super().writerow({key: spreadsheet_cell(value) for key, value in rowdict.items()})

    def writerows(self, rowdicts):
        for row in rowdicts:
            self.writerow(row)
