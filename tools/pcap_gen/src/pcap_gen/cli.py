"""命令行入口：`pcap-gen generate ...`"""

from __future__ import annotations

from pathlib import Path

import typer

from .scenarios import gen_normal_session

app = typer.Typer()


@app.command()
def normal(
    dev_ser: str = typer.Option("DEMO-SN-0001"),  # noqa: B008
    slave: int = typer.Option(1),  # noqa: B008
    frames: int = typer.Option(100),  # noqa: B008
    out_dir: Path = typer.Option(Path("corpus/generated")),  # noqa: B008
    seed: int = typer.Option(42),  # noqa: B008
) -> None:
    """生成一条"注册 + N 次轮询 + 心跳"的正常会话。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"normal_{dev_ser}_{frames}_seed{seed}"
    gen_normal_session(
        dev_ser=dev_ser,
        slave=slave,
        frames_count=frames,
        out_pcap=out_dir / f"{name}.pcap",
        out_expected=out_dir / f"{name}.expected.json",
        seed=seed,
    )
    typer.echo(f"wrote: {out_dir / name}.pcap and .expected.json")


if __name__ == "__main__":
    app()
