# AGENTS.md

本文件约定在本仓库工作的 AI/自动化代理需遵守的规范。除非用户另有要求，默认遵循。

## Docstring 规范（必须）

- **主要函数/方法/类必须有 docstring**：公开 API、有业务含义、或名称不足以自解释的，都要写；
  仅仅显而易见的 dunder（如 `__len__`、`__enter__`）、`__init__`，以及一行即可自解释的私有 helper，可以省略。
- **每个模块顶部写 1 行模块 docstring**，说明该模块职责；若有 `from __future__ import annotations`，
  模块 docstring 必须放在它之前。
- **docstring 不超过 3 行**（含首行与三引号行），简洁说明「做什么 / 返回什么 / 关键边界」。
- **数据类必须逐字段说明**：pydantic 模型、dataclass、TypedDict 等以承载数据为主的类，其 docstring
  末尾必须有 `Attributes:` 段，逐字段解释含义（含单位、取值范围与 `None` 的含义）；该段不计入上面的 3 行上限。
- **主要函数必须逐参数说明**：对外可调用的公开函数/方法，其 docstring 末尾须有 `Args:` 段，逐参数说明含义；
  过于简单的函数或含义显而易见的参数可省略。每条参数说明控制在 1 行以内；`Args:` 段不计入上面的 3 行上限。
- **语言与风格与现有代码保持一致**：使用中文，参照 `packages/mikan-health/src/mikan_health/core/releases.py`
  等既有写法。
- 只补充 docstring，**不得顺手改动代码逻辑、函数签名、导入顺序或已有的 docstring**。

## 提交前校验

提交时会经 pre-commit 运行：基础卫生、换行符、ruff、vulture、xenon、mypy、pytest 与覆盖率。
- 手动运行全部检查：`uv run pre-commit run --all-files`
- 提交时 `pre-commit` 需在 venv PATH 中；可直接用 `uv run --all-packages git commit ...`
- 同步依赖请用 `uv sync --all-packages`（普通 `uv sync` 会移除 workspace 成员包，导致 `import mikan_health` 失败）
