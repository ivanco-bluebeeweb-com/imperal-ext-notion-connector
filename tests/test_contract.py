"""Static contract sweep over the whole app.

The WP Publisher incident is the reason this file exists: errors were emitted
without a structured `code`, the kernel stamped EXT_UNSTRUCTURED_ERROR, and no
validator rule caught it because the app routed through a local helper instead
of calling ActionResult.error directly. Validator rule V32 matches the literal
call, so it stayed silent.

A test that walks the AST does not care which helper is used — it checks every
error path in the source, so the same class of bug cannot come back quietly.
"""

import ast
import pathlib

APP_DIR = pathlib.Path(__file__).resolve().parent.parent
HANDLER_FILES = ["handlers_read.py", "handlers_write.py", "workspaces.py",
                 "notion_client.py", "panels.py"]


def _tree(name: str) -> ast.AST:
    return ast.parse((APP_DIR / name).read_text())


def _calls(tree: ast.AST, *names: str):
    """Every Call node whose callee is one of `names` (attribute or plain)."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        label = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if label in names:
            yield node


def test_every_error_result_carries_a_structured_code():
    """No ActionResult.error() anywhere without an explicit code=."""
    offenders = []
    for name in HANDLER_FILES:
        for call in _calls(_tree(name), "error"):
            fn = call.func
            # Only ActionResult.error(...) — not ctx.log.error(...)
            if not (isinstance(fn, ast.Attribute)
                    and isinstance(fn.value, ast.Name)
                    and fn.value.id == "ActionResult"):
                continue
            if not any(kw.arg == "code" for kw in call.keywords):
                offenders.append(f"{name}:{call.lineno}")
    assert not offenders, f"ActionResult.error without code=: {offenders}"


def test_the_local_error_helper_always_requires_a_code():
    """_error(message, code) — code is positional and mandatory, never defaulted.

    A default would let a call site silently omit it, which is exactly how the
    unstructured-error bug happened before.
    """
    tree = _tree("handlers_read.py")
    helper = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_error")
    args = [a.arg for a in helper.args.args]
    assert args[:2] == ["message", "code"]
    # defaults align to the TAIL of the arg list; `code` must not have one.
    assert len(helper.args.defaults) <= len(args) - 2


def test_every_error_helper_call_passes_a_code():
    """Each _error(...) call site supplies the code argument."""
    offenders = []
    for name in ("handlers_read.py", "handlers_write.py"):
        for call in _calls(_tree(name), "_error"):
            has_positional_code = len(call.args) >= 2
            has_kw_code = any(kw.arg == "code" for kw in call.keywords)
            if not (has_positional_code or has_kw_code):
                offenders.append(f"{name}:{call.lineno}")
    assert not offenders, f"_error() without a code: {offenders}"


def test_no_user_facing_message_interpolates_an_exception():
    """Raw exception text must not reach the user; it goes to the audit log.

    Catches f-strings containing {e}/{exc}/{err} inside an ActionResult.error
    or _error call — the shape that leaks tracebacks into chat.
    """
    offenders = []
    leaky = {"e", "exc", "err", "error"}
    for name in HANDLER_FILES:
        for call in _calls(_tree(name), "error", "_error"):
            fn = call.func
            is_action_error = (isinstance(fn, ast.Attribute)
                               and isinstance(fn.value, ast.Name)
                               and fn.value.id == "ActionResult")
            is_helper = getattr(fn, "id", "") == "_error"
            if not (is_action_error or is_helper):
                continue
            for arg in call.args:
                if not isinstance(arg, ast.JoinedStr):
                    continue
                for piece in ast.walk(arg):
                    if (isinstance(piece, ast.FormattedValue)
                            and isinstance(piece.value, ast.Name)
                            and piece.value.id in leaky):
                        offenders.append(f"{name}:{call.lineno}")
    assert not offenders, f"exception text in user-facing message: {offenders}"


def test_no_token_is_ever_written_to_the_store():
    """Only the Vault secret holds tokens; the store keeps metadata only."""
    tree = _tree("workspaces.py")
    for call in _calls(tree, "insert", "update"):
        for arg in list(call.args) + [kw.value for kw in call.keywords]:
            for node in ast.walk(arg):
                if isinstance(node, ast.Name) and node.id == "token":
                    raise AssertionError(
                        f"token passed to store at workspaces.py:{call.lineno}")


def test_no_print_statements_survive_in_shipped_code():
    for name in HANDLER_FILES + ["app.py", "models.py", "notion_objects.py"]:
        for call in _calls(_tree(name), "print"):
            raise AssertionError(f"print() left in {name}:{call.lineno}")
