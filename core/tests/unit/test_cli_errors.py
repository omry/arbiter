from arbiter_core.cli_errors import format_cli_error


def test_format_cli_error_with_area() -> None:
    assert (
        format_cli_error("could not connect", area="connection")
        == "Arbiter connection error: could not connect"
    )


def test_format_cli_error_indents_multiline_details() -> None:
    assert (
        format_cli_error(
            "missing required environment variables:",
            area="env",
            details=["SMTP_USERNAME (arbiter-smtp)"],
        )
        == "Arbiter env error: missing required environment variables:\n"
        "  SMTP_USERNAME (arbiter-smtp)"
    )


def test_format_cli_error_indents_embedded_newlines() -> None:
    assert (
        format_cli_error("first line\nsecond line", area="config")
        == "Arbiter config error: first line\n"
        "  second line"
    )
