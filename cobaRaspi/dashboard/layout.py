from rich.layout import Layout


def make_layout():

    layout = Layout(name="root")

    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=10),
    )

    layout["body"].split_row(
        Layout(name="left"),
        Layout(name="right"),
    )

    layout["left"].split_column(
        Layout(name="system"),
        Layout(name="wifi"),
    )

    layout["right"].split_column(
        Layout(name="status"),
    )

    return layout
