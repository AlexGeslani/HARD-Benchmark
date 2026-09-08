"""Synthetic control-flow check against pinned sources; not model qualification."""
import ast
import copy
import itertools
import shutil
from pathlib import Path
from types import SimpleNamespace

from hard1.v11 import families


def test_engineering_config_accepts_explicit_null_native_limits(tmp_path):
    import json
    from hard1.v11.runner import load_config
    config = json.loads(Path('config/hard1-v1.1.example.json').read_text())
    config.update(native_max_turns=None, native_max_steps=None)
    path = tmp_path / 'pilot.json'
    path.write_text(json.dumps(config))
    assert load_config(path)['native_max_steps'] is None
    del config['native_max_steps']
    path.write_text(json.dumps(config))
    try:
        load_config(path)
    except ValueError:
        pass
    else:
        raise AssertionError('Missing declaration was accepted')


def test_native_limits_allow_none_without_replacing_semantic_logic(tmp_path):
    adapt = getattr(families, "apply_native_limits_overlay", None)
    assert callable(adapt), "native None-limit adaptation is missing"
    roots = {}
    for family, relative in (("MCP", "client/agent.py"), ("T3", "src/tau2/orchestrator/orchestrator.py"), ("T3", "src/tau2/data_model/simulation.py")):
        target = tmp_path / family / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path('.hard1/sources') / family / relative, target)
        roots[family] = tmp_path / family
    before = {family: (root / ("client/agent.py" if family == "MCP" else "src/tau2/orchestrator/orchestrator.py")).read_text() for family, root in roots.items()}
    receipts = [adapt(root, family) for family, root in roots.items()]
    assert all(receipts)
    # Execute the exact native iterator, keeping the entire native loop body identical.
    def native_loop(text):
        return next(n for n in ast.walk(ast.parse(text)) if isinstance(n, ast.For) and isinstance(n.target, ast.Name) and n.target.id == 'idx')
    old = native_loop(before['MCP'])
    new = native_loop((roots['MCP'] / 'client/agent.py').read_text())
    assert ast.dump(ast.Module(body=old.body, type_ignores=[])) == ast.dump(ast.Module(body=new.body, type_ignores=[]))
    expression = compile(ast.Expression(new.iter), '<native MCP iterator>', 'eval')
    assert list(eval(expression, {'max_turns': 2})) == [0, 1]
    assert list(itertools.islice(eval(expression, {'max_turns': None}), 105)) == list(range(105))
    # Execute the complete actual native termination method with no network/mock LLM.
    tree = ast.parse((roots['T3'] / 'src/tau2/orchestrator/orchestrator.py').read_text())
    method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_check_termination' and len(n.body) > 2)
    namespace = {'Role': SimpleNamespace(ENV='env'), 'TerminationReason': SimpleNamespace(MAX_STEPS='steps', TOO_MANY_ERRORS='errors')}
    exec(compile(ast.Module(body=[method], type_ignores=[]), '<native T3 termination>', 'exec'), namespace)
    obj = SimpleNamespace(to_role='agent', step_count=105, max_steps=None, num_errors=0, max_errors=10, done=False, termination_reason=None, _check_timeout=lambda: None)
    namespace['_check_termination'](obj)
    assert not obj.done
    obj.max_steps = 2
    namespace['_check_termination'](obj)
    assert obj.done and obj.termination_reason == 'steps'
    # Reverse only the cap guard: all other native termination semantics are untouched.
    patched = (roots['T3'] / 'src/tau2/orchestrator/orchestrator.py').read_text()
    assert patched.replace('self.max_steps is not None and self.step_count >= self.max_steps', 'self.step_count >= self.max_steps') == before['T3']
