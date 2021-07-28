from asyncio import create_subprocess_exec, shield
from asyncio.locks import Lock
from asyncio.subprocess import Process
from contextlib import suppress
from itertools import chain
from json import dumps, loads
from os import linesep
from subprocess import DEVNULL, PIPE
from typing import Any, AsyncIterator, Iterator, Optional, Sequence

from pynvim_pp.lib import go
from std2.itertools import chunk
from std2.pickle import new_decoder, new_encoder

from ...shared.runtime import Supervisor
from ...shared.runtime import Worker as BaseWorker
from ...shared.settings import Options, TabnineClient
from ...shared.types import Completion, Context, ContextualEdit
from .install import T9_BIN, ensure_installed
from .types import ReqL1, ReqL2, Request, Response

_VERSION = "3.2.28"

_DECODER = new_decoder(Response, strict=False)
_ENCODER = new_encoder(Request)


def _encode(options: Options, context: Context, limit: int) -> Any:
    row, _ = context.position
    before = linesep.join(chain(context.lines_before, (context.line_before,)))
    after = linesep.join(chain((context.line_after,), context.lines_after))
    ibg = row - options.context_lines <= 0
    ieof = row + options.context_lines >= context.line_count

    l2 = ReqL2(
        filename=context.filename,
        before=before,
        after=after,
        region_includes_beginning=ibg,
        region_includes_end=ieof,
        max_num_results=None if context.manual else limit,
    )
    l1 = ReqL1(Autocomplete=l2)
    req = Request(request=l1, version=_VERSION)
    return _ENCODER(req)


def _decode(client: TabnineClient, reply: Any) -> Iterator[Completion]:
    resp: Response = _DECODER(reply)

    for result in resp.results:
        edit = ContextualEdit(
            old_prefix=resp.old_prefix,
            new_prefix=result.new_prefix,
            old_suffix=result.old_suffix,
            new_text=result.new_prefix + result.new_suffix,
        )
        label = (result.new_prefix.splitlines() or ("",))[-1] + (
            result.new_suffix.splitlines() or ("",)
        )[0]
        cmp = Completion(
            source=client.short_name,
            tie_breaker=client.tie_breaker,
            label=label,
            sort_by=edit.new_text,
            primary_edit=edit,
        )
        yield cmp


async def _proc() -> Process:
    proc = await create_subprocess_exec(T9_BIN, stdin=PIPE, stdout=PIPE, stderr=DEVNULL)
    return proc


class Worker(BaseWorker[TabnineClient, None]):
    def __init__(
        self, supervisor: Supervisor, options: TabnineClient, misc: None
    ) -> None:
        self._lock, self._installed = Lock(), False
        self._proc: Optional[Process] = None
        super().__init__(supervisor, options=options, misc=misc)
        go(supervisor.nvim, aw=self._install())

    async def _install(self) -> None:
        self._installed = await ensure_installed(
            self._options.download_retries,
            timeout=self._options.download_timeout,
        )

    async def _comm(self, json: str) -> str:
        if self._proc:
            async with self._lock:
                if self._proc.stdin:
                    self._proc.stdin.write(json.encode())
                    self._proc.stdin.write(b"\n")
                    await self._proc.stdin.drain()
                if self._proc.stdout:
                    out = await self._proc.stdout.readline()
                    return out.decode()
                else:
                    return "{}"
        else:
            return "{}"

    async def work(self, context: Context) -> AsyncIterator[Sequence[Completion]]:
        if not self._installed:
            pass
        else:
            if not self._proc:
                self._proc = await _proc()

            req = _encode(
                self._supervisor.options,
                context=context,
                limit=self._supervisor.options.max_results,
            )
            json = dumps(req, check_circular=False, ensure_ascii=False)
            try:
                json = await shield(self._comm(json))
            except (BrokenPipeError, ConnectionResetError):
                with suppress(ProcessLookupError):
                    self._proc.kill()
                await self._proc.wait()
                self._proc = await _proc()
            else:
                reply = loads(json)
                for chunked in chunk(
                    _decode(self._options, reply=reply),
                    n=self._supervisor.options.max_results,
                ):
                    yield chunked
