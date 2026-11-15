"""Series monitoring (``kaji watch``) for opt-in repositories (Issue #543).

run と series の事実は ``kaji_harness.runstate.query`` だけから取得し、kaji の内部ファイル
（run の log・process 記録等）を直接読まない。純粋関数（``labels`` / ``detect``）と副作用の層を分ける。
"""

from __future__ import annotations
