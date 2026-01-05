"""Dependency resolution with cycle detection for taskpy."""

from __future__ import annotations

from .task import Task, TaskRegistry


class CyclicDependencyError(Exception):
    """Raised when a cyclic dependency is detected."""

    def __init__(self, cycle: list[str]) -> None:
        self.cycle = cycle
        cycle_str = " -> ".join(cycle)
        super().__init__(f"Cyclic dependency detected: {cycle_str}")


class DependencyResolver:
    """Resolves task dependencies and detects cycles."""

    def __init__(self, registry: TaskRegistry) -> None:
        self.registry = registry

    def get_dependencies(self, task: Task) -> list[Task]:
        """Get immediate task dependencies based on input files and depends."""
        deps = []
        seen = set()

        # Task dependencies (from depends field)
        for dep_name in task.depends:
            if dep_name not in seen:
                dep_task = self.registry.get(dep_name)
                if dep_task and dep_task.name != task.name:
                    deps.append(dep_task)
                    seen.add(dep_name)

        # File-based dependencies
        for input_path in task.inputs:
            dep_task = self.registry.get_by_output(input_path)
            if dep_task and dep_task.name != task.name and dep_task.name not in seen:
                deps.append(dep_task)
                seen.add(dep_task.name)

        return deps

    def resolve(self, target: Task) -> list[Task]:
        """
        Resolve all dependencies for a target task.

        Returns tasks in execution order (dependencies first).
        Raises CyclicDependencyError if a cycle is detected.
        """
        result: list[Task] = []
        visited: set[str] = set()
        in_stack: set[str] = set()
        stack: list[str] = []

        def visit(task: Task) -> None:
            if task.name in visited:
                return

            if task.name in in_stack:
                # Found a cycle - extract it
                cycle_start = stack.index(task.name)
                cycle = stack[cycle_start:] + [task.name]
                raise CyclicDependencyError(cycle)

            in_stack.add(task.name)
            stack.append(task.name)

            # Visit dependencies first
            for dep in self.get_dependencies(task):
                visit(dep)

            stack.pop()
            in_stack.remove(task.name)
            visited.add(task.name)
            result.append(task)

        visit(target)
        return result

    def build_dependency_graph(self, target: Task) -> dict[str, list[str]]:
        """Build a dependency graph for visualization."""
        graph: dict[str, list[str]] = {}
        visited: set[str] = set()

        def visit(task: Task) -> None:
            if task.name in visited:
                return
            visited.add(task.name)

            deps = self.get_dependencies(task)
            graph[task.name] = [d.name for d in deps]

            for dep in deps:
                visit(dep)

        visit(target)
        return graph

    def to_dot(self, target: Task) -> str:
        """Generate a DOT graph representation."""
        lines = ["digraph tasks {", "    rankdir=BT;"]

        visited_tasks: set[str] = set()
        visited_files: set[str] = set()

        def sanitize(name: str) -> str:
            return name.replace(":", "_").replace("/", "_").replace(".", "_")

        def visit(task: Task) -> None:
            if task.name in visited_tasks:
                return
            visited_tasks.add(task.name)

            # Task node (box shape)
            task_id = f"task_{sanitize(task.name)}"
            lines.append(f'    {task_id} [label="{task.name}" shape=box];')

            # Output files
            for out in task.outputs:
                out_str = str(out)
                if out_str not in visited_files:
                    visited_files.add(out_str)
                    file_id = f"file_{sanitize(out_str)}"
                    lines.append(f'    {file_id} [label="{out_str}" shape=ellipse];')
                file_id = f"file_{sanitize(out_str)}"
                lines.append(f"    {task_id} -> {file_id};")

            # Task dependencies (from depends field)
            for dep_name in task.depends:
                dep_task = self.registry.get(dep_name)
                if dep_task:
                    dep_id = f"task_{sanitize(dep_name)}"
                    lines.append(f"    {dep_id} -> {task_id};")
                    visit(dep_task)

            # Input files and their producing tasks
            for inp in task.inputs:
                inp_str = str(inp)
                if inp_str not in visited_files:
                    visited_files.add(inp_str)
                    file_id = f"file_{sanitize(inp_str)}"
                    lines.append(f'    {file_id} [label="{inp_str}" shape=ellipse];')
                file_id = f"file_{sanitize(inp_str)}"
                lines.append(f"    {file_id} -> {task_id};")

                # Visit producing task
                dep_task = self.registry.get_by_output(inp)
                if dep_task:
                    visit(dep_task)

        visit(target)
        lines.append("}")
        return "\n".join(lines)
