"""Disposable local task contexts.

A ``TaskContext`` bundles a fresh ``TaskRegistry`` + ``DependencyResolver`` +
``Executor`` behind a library API. It is a non-global alternative to the
singleton ``pymake.task`` registry used by ``Makefile.py`` — construct one
inside a Python function, register tasks against it, call ``ctx.run()``, and
let it go out of scope.

Relative paths in ``inputs=`` / ``outputs=`` / ``touch=`` resolve against
``ctx.cwd`` (defaults to the process cwd). Absolute paths pass through.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from .executor import Executor
from .resolver import DependencyResolver
from .task import Task, TaskRegistry

if TYPE_CHECKING:
    from rich.console import Console as RichConsole


TaskFunc = Callable[..., None]


def _resolve(path: str | Path, cwd: Path) -> Path:
    """Resolve *path* against *cwd* if relative; absolute paths pass through."""
    p = Path(path)
    if p.is_absolute():
        return p
    return cwd / p


class _ContextDecorator:
    """Decorator handle returned by ``TaskContext.task``.

    Usable as ``@ctx.task(inputs=..., outputs=...)`` and
    ``ctx.task.register(func, ...)``. Mirrors the global ``pymake.task``
    decorator surface, scoped to a single context.
    """

    def __init__(self, ctx: TaskContext) -> None:
        self._ctx = ctx

    def __call__(
        self,
        inputs: Sequence[str | Path | TaskFunc] = (),
        outputs: Sequence[str | Path] = (),
        run_if: Callable[[], bool] | None = None,
        run_if_not: Callable[[], bool] | None = None,
        touch: str | Path | None = None,
        *,
        name: str | None = None,
    ) -> Callable[[TaskFunc], TaskFunc]:
        def decorator(func: TaskFunc) -> TaskFunc:
            self._ctx.register(
                func,
                name=name,
                inputs=inputs,
                outputs=outputs,
                run_if=run_if,
                run_if_not=run_if_not,
                touch=touch,
            )
            return func

        return decorator

    def register(
        self,
        func: TaskFunc,
        *,
        name: str | None = None,
        inputs: Sequence[str | Path | TaskFunc] = (),
        outputs: Sequence[str | Path] = (),
        run_if: Callable[[], bool] | None = None,
        run_if_not: Callable[[], bool] | None = None,
        touch: str | Path | None = None,
    ) -> Task:
        return self._ctx.register(
            func,
            name=name,
            inputs=inputs,
            outputs=outputs,
            run_if=run_if,
            run_if_not=run_if_not,
            touch=touch,
        )

    def default(self, task_or_name: str | TaskFunc | Task) -> None:
        self._ctx.default(task_or_name)


class TaskContext:
    """A disposable task registry scoped to a caller-supplied ``cwd``.

    Construct via :func:`pymake.context`. Register tasks via
    ``ctx.task`` (decorator) or ``ctx.register`` (dynamic). Run via
    ``ctx.run``. The context holds a private ``TaskRegistry`` — nothing
    spills into the global ``pymake.task`` singleton.
    """

    def __init__(self, *, cwd: Path | None = None) -> None:
        self.cwd: Path = Path(cwd).resolve() if cwd is not None else Path.cwd()
        self.registry: TaskRegistry = TaskRegistry()
        self._decorator = _ContextDecorator(self)

    @property
    def task(self) -> _ContextDecorator:
        """Decorator + ``.register`` / ``.default`` bound to this context."""
        return self._decorator

    def register(
        self,
        func: TaskFunc,
        *,
        name: str | None = None,
        inputs: Sequence[str | Path | TaskFunc] = (),
        outputs: Sequence[str | Path] = (),
        run_if: Callable[[], bool] | None = None,
        run_if_not: Callable[[], bool] | None = None,
        touch: str | Path | None = None,
    ) -> Task:
        resolved_inputs: list[str | Path | TaskFunc] = []
        for inp in inputs:
            if callable(inp):
                resolved_inputs.append(inp)
            else:
                resolved_inputs.append(_resolve(inp, self.cwd))

        resolved_outputs = [_resolve(o, self.cwd) for o in outputs]
        resolved_touch: Path | None = (
            _resolve(touch, self.cwd) if touch is not None else None
        )

        return self.registry.register(
            func,
            name=name,
            inputs=resolved_inputs,
            outputs=resolved_outputs,
            run_if=run_if,
            run_if_not=run_if_not,
            touch=resolved_touch,
        )

    def default(self, task_or_name: str | TaskFunc | Task) -> None:
        if isinstance(task_or_name, Task):
            self.registry.default(task_or_name.name)
        else:
            self.registry.default(task_or_name)

    def _resolve_target(self, target: str | TaskFunc | Task | None) -> Task:
        if target is None:
            name = self.registry.default_task()
            if name is None:
                raise ValueError(
                    "ctx.run(): no target given and no default registered "
                    "(use ctx.default(...) or pass target=)"
                )
            return self.registry.find_target_or_raise(name)
        if isinstance(target, Task):
            return target
        if callable(target):
            return self.registry.find_target_or_raise(target.__name__)
        return self.registry.find_target_or_raise(target)

    def run(
        self,
        target: str | TaskFunc | Task | None = None,
        *,
        force: bool = False,
        force_from: str | TaskFunc | Task | None = None,
        dry_run: bool = False,
        parallel: bool = False,
        jobs: int | None = None,
        console: RichConsole | None = None,
    ) -> bool:
        """Run *target* (or the registered default).

        Returns ``True`` if any task body was executed. Mirrors the pymake
        CLI's ``run`` / ``-B`` / ``redo`` / ``which --dry`` semantics via
        the library surface.
        """
        if force and force_from is not None:
            raise ValueError("ctx.run(): pass force= OR force_from=, not both")

        resolved_target = self._resolve_target(target)
        resolver = DependencyResolver(self.registry)

        if dry_run:
            self._print_plan(
                resolved_target, resolver,
                force=force, force_from=force_from, console=console,
            )
            return False

        if force_from is not None:
            return self._run_force_from(
                resolved_target, force_from,
                resolver=resolver, parallel=parallel, jobs=jobs,
            )

        use_parallel = parallel or jobs is not None
        executor = Executor(
            self.registry,
            parallel=use_parallel,
            max_workers=jobs,
            force=force,
        )
        return executor.run(resolved_target)

    def _run_force_from(
        self,
        target: Task,
        force_from: str | TaskFunc | Task,
        *,
        resolver: DependencyResolver,
        parallel: bool,
        jobs: int | None,
    ) -> bool:
        if isinstance(force_from, Task):
            anchor = force_from
        elif callable(force_from):
            anchor = self.registry.find_target_or_raise(force_from.__name__)
        else:
            anchor = self.registry.find_target_or_raise(force_from)

        force_set: set[str] = set(resolver.transitive_dependents(anchor))
        force_set.add(anchor.name)

        execution_order = resolver.resolve(target)

        executor = Executor(
            self.registry,
            parallel=False,  # per-task force toggling needs sequential control
            force=False,
        )
        _ = parallel, jobs  # accepted for API parity; sequential by design here

        # Validate before running, mirroring Executor.run()
        executor._validate_inputs_producible(execution_order)

        any_executed = False
        for t in execution_order:
            executor.force = t.name in force_set
            if executor._execute_task(t):
                any_executed = True
        return any_executed

    def _print_plan(
        self,
        target: Task,
        resolver: DependencyResolver,
        *,
        force: bool,
        force_from: str | TaskFunc | Task | None,
        console: RichConsole | None,
    ) -> None:
        from rich.console import Console as _Console

        if force_from is not None:
            if isinstance(force_from, Task):
                anchor_name = force_from.name
            elif callable(force_from):
                anchor_name = force_from.__name__
            else:
                anchor_name = force_from
            anchor = self.registry.find_target_or_raise(anchor_name)
            force_set: set[str] = set(resolver.transitive_dependents(anchor))
            force_set.add(anchor.name)
        else:
            force_set = set()

        con = console or _Console()
        con.print(f"[bold]plan[/bold] for [cyan]{target.name}[/cyan] (dry run)")
        for t in resolver.resolve(target):
            forced = force or t.name in force_set
            would_run = forced or t.should_run(force=False)
            marker = "[red]*[/red]" if would_run else "[dim]-[/dim]"
            reason = " (forced)" if forced else ""
            con.print(f"  {marker} {t.name}{reason}")

    def which(
        self,
        target: str | TaskFunc | Task | None = None,
        *,
        dependents: bool = False,
    ) -> list[str]:
        """Return task names in execution order for *target* (or default).

        With ``dependents=True``, return tasks that depend on *target*
        instead — matches ``pymake which -d``.
        """
        t = self._resolve_target(target)
        resolver = DependencyResolver(self.registry)
        if dependents:
            names = resolver.transitive_dependents(t)
            names.add(t.name)
            return sorted(names)
        return [x.name for x in resolver.resolve(t)]

    def graph(
        self, target: str | TaskFunc | Task | None = None
    ) -> str:
        """Return a DOT graph string for *target* (or the default)."""
        t = self._resolve_target(target)
        return DependencyResolver(self.registry).to_dot(t)

    def clean(
        self,
        target: str | TaskFunc | Task | None = None,
        *,
        up: bool = False,
        down: bool = False,
        dry: bool = False,
        all_tasks: bool = False,
    ) -> list[Path]:
        """Delete output files for *target* (or everything with ``all_tasks``).

        Returns the list of files that were (or would be, with
        ``dry=True``) deleted. Mirrors ``pymake clean`` semantics.
        """
        if all_tasks and target is not None:
            raise ValueError("ctx.clean(): cannot combine all_tasks=True with a target")
        if all_tasks and (up or down):
            raise ValueError("ctx.clean(): up/down cannot be used with all_tasks=True")

        files: set[Path] = set()
        if all_tasks:
            for t in self.registry.all_tasks():
                files.update(t.outputs)
        else:
            anchor = self._resolve_target(target)
            tasks_to_clean: list[Task] = [anchor]
            resolver = DependencyResolver(self.registry)
            if up:
                for dep in resolver.resolve(anchor):
                    if dep.name != anchor.name:
                        tasks_to_clean.append(dep)
            if down:
                for dep_name in resolver.transitive_dependents(anchor):
                    if dep_name == anchor.name:
                        continue
                    dep_task = self.registry.get(dep_name)
                    if dep_task:
                        tasks_to_clean.append(dep_task)
            for t in tasks_to_clean:
                files.update(t.outputs)

        existing = sorted(f for f in files if f.exists())
        if not dry:
            for f in existing:
                f.unlink()
        return existing


def context(*, cwd: Path | str | None = None) -> TaskContext:
    """Create a fresh, disposable ``TaskContext``.

    ``cwd`` — base directory for resolving relative ``inputs=`` /
    ``outputs=`` / ``touch=`` paths. Defaults to the process cwd.
    """
    if cwd is not None and not isinstance(cwd, Path):
        cwd = Path(cwd)
    return TaskContext(cwd=cwd)


__all__ = ["TaskContext", "context"]
