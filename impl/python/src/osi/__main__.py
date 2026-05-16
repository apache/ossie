"""Allow ``python -m osi ...`` to dispatch to :func:`osi.cli.main`."""

from __future__ import annotations

from osi.cli import main

raise SystemExit(main())
