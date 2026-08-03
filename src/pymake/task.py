"""Task definition and registry for pymake."""

from __future__ import annotations

import dataclasses
import inspect
import os
import warnings
from collections.abc import Callable, Sequence
from pathlib import Path
from types import UnionType
from typing import (
    Any,
    Protocol,
    Union,
    get_args,
    get_origin,
    get_type_hints,
    overload,
)

from .inputs import Input, input_defsite, is_input

SUPPORTED_VAR_TYPES = {str, int, float, bool, Path}

# What ``inputs=`` accepts: a path, a task reference (callable or quoted
# name), or an Input object (id + fingerprint()).
InputArg = str | Path | Callable[..., None] | Input

GROUP_SEPARATORS = (".", "_")


def _external_stacklevel() -> int:
    """Stacklevel attributing a warning to the first frame outside pymake.

    Registration reaches ``register()`` through several internal shims
    (decorator, group registrar, context), so a fixed stacklevel cannot
    point at the Makefile line. Walk out of the package instead.
    """
    package_dir = os.path.dirname(os.path.abspath(__file__))
    frame = inspect.currentframe()
    if frame is not None:
        frame = frame.f_back  # the function that will call warnings.warn
    level = 1
    while frame is not None:
        filename = os.path.abspath(frame.f_code.co_filename)
        if os.path.dirname(filename) != package_dir and not filename.startswith(
            package_dir + os.sep
        ):
            return level
        frame = frame.f_back
        level += 1
    return 1


def _warn_legacy_predicates(
    task_name: str,
    run_if: Callable[[], bool] | None,
    run_if_not: Callable[[], bool] | None,
) -> None:
    """Deprecation (and severed-wrapper) warnings for run_if predicates."""
    stacklevel = _external_stacklevel()
    for param, predicate in (("run_if", run_if), ("run_if_not", run_if_not)):
        if predicate is None:
            continue
        warnings.warn(
            f"Task '{task_name}': {param} is deprecated — express staleness "
            "as inputs (value(), git(), or a custom Input), or force with "
            "'pymake redo' / -B",
            FutureWarning,
            stacklevel=stacklevel,
        )

    # The severed-wrapper signature: a run_if that reaches a TreeDigest but
    # exposes no .commit. The digest file never settles and the task
    # rebuilds forever — the exact silent failure the Input contract
    # retires. Hard warning, every registration.
    from .digest import severed_commit_wrapper

    digest = severed_commit_wrapper(run_if)
    if digest is not None:
        warnings.warn(
            f"Task '{task_name}': run_if wraps a TreeDigest "
            f"({digest.digest_path}) but exposes no .commit — the digest "
            "will never settle and the task will re-run forever. Pass the "
            "digest's .changed directly, or migrate to "
            "inputs=[git(...)/value(...)]",
            UserWarning,
            stacklevel=stacklevel,
        )


@dataclasses.dataclass(frozen=True)
class TaskVar:
    """A variable extracted from a task function signature."""

    name: str
    type: type[Any]
    default: Any
    is_optional: bool


def _is_optional(annotation: Any) -> bool:
    """True if annotation is exactly T | None / Optional[T]."""
    origin = get_origin(annotation)
    if origin not in (Union, UnionType):
        return False

    args = get_args(annotation)
    if len(args) != 2:
        return False

    return any(arg is type(None) for arg in args)


def _unwrap_optional(annotation: Any) -> Any:
    """Return T from Optional[T]."""
    args = get_args(annotation)
    for arg in args:
        if arg is not type(None):
            return arg
    return annotation


def vars_from_signature(func: Callable[..., None]) -> tuple[TaskVar, ...]:
    """Extract and validate task variables from function signature."""
    signature = inspect.signature(func)
    type_hints = get_type_hints(func)
    result: list[TaskVar] = []

    for param in signature.parameters.values():
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            raise ValueError(f"Task '{func.__name__}': *args/**kwargs not supported")

        annotation: Any = type_hints.get(param.name, param.annotation)
        is_optional = False

        if annotation is not inspect.Parameter.empty and _is_optional(annotation):
            annotation = _unwrap_optional(annotation)
            is_optional = True

        if annotation is inspect.Parameter.empty:
            annotation = str

        if annotation not in SUPPORTED_VAR_TYPES:
            raise ValueError(
                f"Task '{func.__name__}': unsupported type {annotation} "
                f"for var '{param.name}'"
            )

        if param.default is inspect.Parameter.empty:
            if not is_optional:
                raise ValueError(
                    f"Task '{func.__name__}': var '{param.name}' "
                    "must have a default value or be Optional"
                )
            default = None
        else:
            default = param.default

        result.append(
            TaskVar(
                name=param.name,
                type=annotation,
                default=default,
                is_optional=is_optional,
            )
        )

    return tuple(result)


def method_basename(func: Callable[..., Any]) -> str:
    """Return ``func.__name__``, rejecting callables that cannot name a task.

    Lambdas have no meaningful name, and a function defined inside another
    function (``<locals>`` in its ``__qualname__``) is usually a throwaway
    closure. Both must be registered with an explicit ``name=``. Bound
    methods are always fine — the class supplies the namespace.
    """
    name = getattr(func, "__name__", None)
    if not isinstance(name, str) or not name:
        raise ValueError(
            f"Cannot infer a task name from {func!r}: pass name= to register it"
        )

    if name == "<lambda>":
        raise ValueError("Cannot infer a task name from a lambda: pass name=")

    qualname = getattr(func, "__qualname__", "")
    if not inspect.ismethod(func) and "<locals>" in qualname:
        raise ValueError(
            f"Cannot infer a task name from local function '{qualname}': pass name="
        )

    return name


def infer_task_name(func: Callable[..., Any]) -> str:
    """Infer a task name from a callable.

    A bound method names its group after its **runtime** class —
    ``type(func.__self__).__name__ + "." + func.__name__`` — so a subclass
    renames the whole group and an inherited method still lands in the
    subclass's namespace. A plain function keeps ``func.__name__``.
    """
    name = method_basename(func)

    if inspect.ismethod(func):
        owner = func.__self__
        owner_name = owner.__name__ if isinstance(owner, type) else type(owner).__name__
        return f"{owner_name}.{name}"

    return name


@dataclasses.dataclass
class Task:
    """A build task with inputs, outputs, and execution function."""

    name: str
    func: Callable[..., None]
    inputs: tuple[Path, ...]
    outputs: tuple[Path, ...]
    vars: tuple[TaskVar, ...] = ()
    run_if: Callable[[], bool] | None = None
    run_if_not: Callable[[], bool] | None = None
    doc: str | None = None
    touch: Path | None = None
    depends: tuple[str, ...] = ()
    string_inputs: tuple[str, ...] = ()
    input_objects: tuple[Input, ...] = ()

    def ref_hint(self, input_path: Path) -> str | None:
        """Diagnose *input_path* as a task reference that resolved to nothing.

        A dotted, separator-free ``str`` input looks like a ``"Ns.method"``
        task reference. If it survived ``TaskRegistry.finalize()`` as a file
        input, no task by that name exists — say so instead of complaining
        about a missing file.
        """
        ref = str(input_path)
        if ref not in self.string_inputs:
            return None
        if "." not in ref or "/" in ref or os.sep in ref:
            return None
        return f"no task and no file named '{ref}' — is its group registered?"

    @property
    def is_phony(self) -> bool:
        """Task is phony if it has no outputs (always runs)."""
        return len(self.outputs) == 0

    def should_run(self, force: bool = False) -> bool:
        """Determine if this task should run based on file timestamps."""
        if force:
            return True

        # No outputs = phony target, always run
        if self.is_phony:
            return True

        # Check if any output is missing
        for out in self.outputs:
            if not out.exists():
                return True

        # No inputs = only run if output doesn't exist (already checked above)
        if not self.inputs:
            return False

        # Get the oldest output mtime
        oldest_output = min(out.stat().st_mtime for out in self.outputs)

        # Check if any input is newer than the oldest output
        for inp in self.inputs:
            if inp.exists() and inp.stat().st_mtime > oldest_output:
                return True

        return False


class SupportsRegister(Protocol):
    """The registration surface a :class:`GroupRegistrar` writes into."""

    def register(
        self,
        func: Callable[..., None],
        *,
        name: str | None = ...,
        inputs: Sequence[InputArg] = ...,
        outputs: Sequence[str | Path] = ...,
        run_if: Callable[[], bool] | None = ...,
        run_if_not: Callable[[], bool] | None = ...,
        touch: str | Path | None = ...,
    ) -> Task: ...


@dataclasses.dataclass(frozen=True)
class GroupRegistrar:
    """Registers callables under a fixed namespace.

    A plain value object — it holds no registry state, so two registrars for
    the same namespace are fine (duplicate task *names* still raise).
    ``registrar.task(fn, ...)`` takes exactly the arguments of a bare
    ``task(fn, ...)`` call; only the name differs, being
    ``namespace + sep + fn.__name__`` instead of the inferred class name.
    """

    registry: SupportsRegister
    namespace: str
    sep: str = "."

    def name_for(self, func: Callable[..., Any]) -> str:
        """The registered name this group would give *func*."""
        return f"{self.namespace}{self.sep}{method_basename(func)}"

    def task(
        self,
        func: Callable[..., None],
        inputs: Sequence[InputArg] = (),
        outputs: Sequence[str | Path] = (),
        run_if: Callable[[], bool] | None = None,
        run_if_not: Callable[[], bool] | None = None,
        touch: str | Path | None = None,
        *,
        name: str | None = None,
    ) -> Task:
        """Register *func* as ``<namespace><sep><func name>`` (or *name*)."""
        return self.registry.register(
            func,
            name=name if name is not None else self.name_for(func),
            inputs=inputs,
            outputs=outputs,
            run_if=run_if,
            run_if_not=run_if_not,
            touch=touch,
        )


def make_group(
    registry: SupportsRegister, namespace: str, sep: str = "."
) -> GroupRegistrar:
    """Validate group parameters and build a :class:`GroupRegistrar`."""
    if not isinstance(namespace, str) or not namespace:
        raise ValueError("Task group namespace must be a non-empty string")
    if not namespace.isidentifier():
        raise ValueError(
            f"Task group namespace '{namespace}' must be a plain identifier "
            "(one level, no dots)"
        )
    if sep not in GROUP_SEPARATORS:
        raise ValueError(
            f"Task group separator {sep!r} is not supported: use '.' or '_'"
        )
    return GroupRegistrar(registry, namespace, sep)


class TaskRegistry:
    """Registry for all tasks."""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._output_to_task: dict[Path, str] = {}
        self._default: str | None = None
        self._callable_names: dict[Any, str] = {}
        # Input-object id registry: id -> (object, definition site). Applies
        # ONLY to Input objects — paths and task names live in their own
        # namespaces and never collide with a user id.
        self._input_ids: dict[str, tuple[Any, str]] = {}

    def default(self, name: str | Callable[..., None]) -> None:
        """Set the default task to run when no target is specified.

        Accepts a task name (``"Windows.build_app"``), a task function, or a
        bound method registered with an inferred/group name.
        """
        if callable(name):
            self._default = self.name_of(name)
        else:
            self._default = name

    def name_of(self, func: Callable[..., Any]) -> str:
        """The registered name of *func*, falling back to ``func.__name__``.

        Bound methods hash and compare by ``__func__`` + ``__self__``, so the
        lookup is instance-precise: two instances of one class registered
        under different namespaces never resolve to each other.
        """
        try:
            recorded = self._callable_names.get(func)
        except TypeError:  # unhashable instance behind a bound method
            recorded = None
        if recorded is not None:
            return recorded
        return str(func.__name__)

    def group(self, namespace: str, sep: str = ".") -> GroupRegistrar:
        """Return a registrar that names tasks ``<namespace><sep><method>``.

        Use it for renames, for parameterized groups (two instances of one
        class), and as the flat-name bridge — ``task.group(namespace="build",
        sep="_")`` produces ``build_app``, byte-identical to a module-level
        task of that name.
        """
        return make_group(self, namespace, sep)

    def default_task(self) -> str | None:
        """Get the default task name."""
        return self._default

    def register(
        self,
        func: Callable[..., None],
        *,
        name: str | None = None,
        inputs: Sequence[InputArg] = (),
        outputs: Sequence[str | Path] = (),
        run_if: Callable[[], bool] | None = None,
        run_if_not: Callable[[], bool] | None = None,
        touch: str | Path | None = None,
    ) -> Task:
        """Register a task with the given parameters."""
        task_name = name or func.__name__

        if run_if is not None or run_if_not is not None:
            _warn_legacy_predicates(task_name, run_if, run_if_not)

        # Partition inputs into their three namespaces: paths, task
        # dependencies, and Input objects. ``str`` inputs are remembered
        # as-is: finalize() later promotes the ones that name a registered
        # task to dependencies. ``Path`` inputs are always files. Input
        # objects are detected by capability (a ``fingerprint`` method), so
        # a fingerprint-bearing object with a broken id fails loudly below
        # instead of being misread as a task reference.
        input_paths: list[Path] = []
        task_depends: list[str] = []
        string_inputs: list[str] = []
        raw_input_objects: list[Any] = []
        for inp in inputs:
            if isinstance(inp, (str, Path)):
                if isinstance(inp, str):
                    string_inputs.append(inp)
                input_paths.append(Path(inp))
            elif is_input(inp):
                raw_input_objects.append(inp)
            elif callable(inp):
                task_depends.append(self.name_of(inp))
            else:
                raise ValueError(
                    f"Task '{task_name}': unsupported input {inp!r} — expected "
                    "a path, a task, or an Input object (id + fingerprint())"
                )

        output_paths = tuple(Path(p) for p in outputs)
        touch_path = Path(touch) if touch else None

        # Touch file is also an output
        if touch_path:
            output_paths = (*output_paths, touch_path)

        # Check for output conflicts
        for out in output_paths:
            out_resolved = out.resolve()
            if out_resolved in self._output_to_task:
                existing = self._output_to_task[out_resolved]
                raise ValueError(
                    f"Output file '{out}' is already produced by task '{existing}'. "
                    f"Cannot register task '{task_name}'."
                )

        task_vars = vars_from_signature(func)

        if task_name in self._tasks:
            raise ValueError(self._duplicate_name_message(task_name, func))

        input_objects = tuple(
            self._checked_input(task_name, obj) for obj in raw_input_objects
        )

        # Create and store task
        task = Task(
            name=task_name,
            func=func,
            inputs=tuple(input_paths),
            outputs=output_paths,
            vars=task_vars,
            run_if=run_if,
            run_if_not=run_if_not,
            doc=func.__doc__,
            touch=touch_path,
            depends=tuple(task_depends),
            string_inputs=tuple(string_inputs),
            input_objects=input_objects,
        )

        self._tasks[task_name] = task
        self._remember_callable(func, task_name)

        # Map outputs to task
        for out in output_paths:
            self._output_to_task[out.resolve()] = task_name

        return task

    def _checked_input(self, task_name: str, obj: Any) -> Input:
        """Enforce the id contract on *obj* at registration, fail loud.

        A missing/empty id is a registration error. Two DIFFERENT Input
        objects claiming the same id is a registration error naming both
        definition sites. Reusing ONE object across tasks is fine — the id
        then names the shared thing.
        """
        input_id = getattr(obj, "id", None)
        where = input_defsite(obj) or repr(obj)
        if not isinstance(input_id, str) or not input_id:
            raise ValueError(
                f"Task '{task_name}': input {obj!r} has a missing or empty id "
                "— the id is the first argument of every input constructor "
                f"(defined at {where})"
            )
        existing = self._input_ids.get(input_id)
        if existing is not None and existing[0] is not obj:
            raise ValueError(
                f"Input id '{input_id}' is claimed by two different inputs: "
                f"{existing[1]} and {where}. Reuse ONE object to share an "
                "input across tasks, or give each input its own id."
            )
        self._input_ids[input_id] = (obj, where)
        result: Input = obj
        return result

    def _duplicate_name_message(self, task_name: str, func: Callable[..., Any]) -> str:
        message = f"Task '{task_name}' is already registered."

        existing = self._tasks[task_name].func
        if (
            inspect.ismethod(func)
            and inspect.ismethod(existing)
            and type(func.__self__) is type(existing.__self__)
            and func.__self__ is not existing.__self__
        ):
            owner = type(func.__self__).__name__
            message += (
                f" Two instances of class '{owner}' infer the same name; give each"
                " its own namespace with task.group(namespace=...) (or pass name=)."
            )

        return message

    def _remember_callable(self, func: Callable[..., Any], task_name: str) -> None:
        """Record callable → registered name for dependency resolution."""
        try:
            self._callable_names[func] = task_name
        except TypeError:  # unhashable instance behind a bound method
            pass

    def finalize(self) -> None:
        """Resolve string task references. Idempotent.

        Every ``str`` input that exactly names a registered task moves from
        the task's file inputs to its dependencies, making quoted names
        (``inputs=["Common.build_assets"]``) forward references: the CLI calls
        this once the Makefile has fully imported, so registration order does
        not matter. ``Path`` inputs are never touched.
        """
        for task in self._tasks.values():
            matched = [
                ref
                for ref in task.string_inputs
                if ref != task.name and ref in self._tasks
            ]
            if not matched:
                continue

            matched_paths = {Path(ref) for ref in matched}
            task.inputs = tuple(p for p in task.inputs if p not in matched_paths)

            depends = list(task.depends)
            for ref in matched:
                if ref not in depends:
                    depends.append(ref)
            task.depends = tuple(depends)

    @overload
    def __call__(
        self,
        fn_or_inputs: Callable[..., None],
        outputs: Sequence[str | Path] = (),
        run_if: Callable[[], bool] | None = None,
        run_if_not: Callable[[], bool] | None = None,
        touch: str | Path | None = None,
        *,
        inputs: Sequence[InputArg] | None = None,
        name: str | None = None,
    ) -> Task: ...

    @overload
    def __call__(
        self,
        fn_or_inputs: Sequence[InputArg] = (),
        outputs: Sequence[str | Path] = (),
        run_if: Callable[[], bool] | None = None,
        run_if_not: Callable[[], bool] | None = None,
        touch: str | Path | None = None,
        *,
        inputs: Sequence[InputArg] | None = None,
        name: str | None = None,
    ) -> Callable[[Callable[..., None]], Callable[..., None]]: ...

    def __call__(
        self,
        fn_or_inputs: Any = (),
        outputs: Sequence[str | Path] = (),
        run_if: Callable[[], bool] | None = None,
        run_if_not: Callable[[], bool] | None = None,
        touch: str | Path | None = None,
        *,
        inputs: Sequence[InputArg] | None = None,
        name: str | None = None,
    ) -> Any:
        """Register a task, either bare or as a decorator.

        ``task(instance.method, inputs=[...])`` registers immediately, naming
        the task ``<ClassName>.<method>`` (``name=`` overrides). Anything
        else returns the familiar ``@task(inputs=..., outputs=...)``
        decorator.
        """
        if callable(fn_or_inputs):
            func: Callable[..., None] = fn_or_inputs
            return self.register(
                func,
                name=name if name is not None else infer_task_name(func),
                inputs=inputs if inputs is not None else (),
                outputs=outputs,
                run_if=run_if,
                run_if_not=run_if_not,
                touch=touch,
            )

        if inputs is not None and fn_or_inputs:
            raise ValueError("task(): pass inputs positionally or as inputs=, not both")
        task_inputs = inputs if inputs is not None else fn_or_inputs

        def decorator(func: Callable[..., None]) -> Callable[..., None]:
            self.register(
                func,
                name=name,
                inputs=task_inputs,
                outputs=outputs,
                run_if=run_if,
                run_if_not=run_if_not,
                touch=touch,
            )
            return func

        return decorator

    def get(self, name: str) -> Task | None:
        """Get a task by name."""
        return self._tasks.get(name)

    def by_output(self, path: str | Path) -> Task | None:
        """Get a task that produces the given output file."""
        resolved = Path(path).resolve()
        task_name = self._output_to_task.get(resolved)
        if task_name:
            return self._tasks.get(task_name)
        return None

    def find_target(self, target: str) -> Task | None:
        """Find a task by name or by output file."""
        # First try by name
        task = self.get(target)
        if task:
            return task

        # Then try by output file
        return self.by_output(target)

    def find_target_or_raise(self, target: str) -> Task:
        """Find a task by name or output file, raising ValueError if not found."""
        task = self.find_target(target)
        if not task:
            raise ValueError(f"Unknown target: {target}")
        return task

    def all_tasks(self) -> list[Task]:
        """Return all registered tasks."""
        return list(self._tasks.values())

    def named_tasks(self) -> list[Task]:
        """Return tasks registered with @task decorator."""
        # For now, return all tasks. In practice, we might want to distinguish
        # between decorator-registered and dynamically-registered tasks.
        return list(self._tasks.values())

    def clear(self) -> None:
        """Clear all registered tasks."""
        self._tasks.clear()
        self._output_to_task.clear()
        self._callable_names.clear()
        self._input_ids.clear()
        self._default = None


# Global task registry
task = TaskRegistry()
