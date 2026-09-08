import ast
import shutil
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

from hard1.runners.common import sha
from hard1.v11.families import apply_t3_mixed_overlay


def _native_methods(path):
    tree = ast.parse(path.read_text())
    wanted = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {"_check_communication_error", "_execute_tool_calls"}:
            node.decorator_list = []
            wanted.append(node)
    module = ast.Module(body=wanted, type_ignores=[]); ast.fix_missing_locations(module)
    class Role(Enum):
        AGENT = "agent"; USER = "user"; ENV = "environment"
    namespace = {"Role": Role, "AgentError": RuntimeError, "UserError": RuntimeError,
                 "ToolCall": object, "ToolMessage": object, "list": list}
    exec(compile(module, str(path), "exec"), namespace)
    return namespace, Role


def test_real_native_overlay_preserves_text_and_executes_tools_in_declared_order(tmp_path):
    source = tmp_path / "T3"
    shutil.copytree(Path(".hard1/sources/T3"), source)
    evaluator = source / "src/tau2/evaluator/evaluator.py"
    evaluator_hash = sha(evaluator.read_bytes())
    receipt = apply_t3_mixed_overlay(source)
    namespace, Role = _native_methods(source / receipt["path"])
    calls = [SimpleNamespace(name="first"), SimpleNamespace(name="second")]
    message = SimpleNamespace(content="visible narration", tool_calls=calls,
                              is_tool_call=lambda: True, has_text_content=lambda: True)
    executed = []
    environment = SimpleNamespace(get_response=lambda call: executed.append(call.name) or SimpleNamespace(error=False))
    obj = SimpleNamespace(from_role=Role.AGENT, message=message, solo_mode=False,
                          agent=SimpleNamespace(is_stop=lambda _: False), environment=environment, num_errors=0)
    namespace["_check_communication_error"](obj)
    results = namespace["_execute_tool_calls"](obj, message.tool_calls)
    assert message.content == "visible narration"
    assert executed == ["first", "second"] and len(results) == 2
    assert sha(evaluator.read_bytes()) == evaluator_hash
