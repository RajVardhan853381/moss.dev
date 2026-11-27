"""Charts for the latency study (PNG, light surface). Reads results/latency_summary.json.

Design (dataviz method): one series is the point -> emphasis form (accent for moss/fastlane-1, neutral gray for the rest);
thin marks, direct labels, recessive grid, text in ink tokens (never series colour); the table view is results/latency_tables.md.
"""
import json, os, textwrap
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "..", "results")
S = json.load(open(os.path.join(RES, "latency_summary.json")))

SURFACE, INK, INK2, GRID, AXIS = "#ffffff", "#191919", "#666666", "#e7e7e7", "#c2c2c2"
ACCENT, MUTED = "#257de0", "#8c8c8c"
plt.rcParams.update({"font.family": "DejaVu Sans", "axes.edgecolor": AXIS, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK, "text.color": INK,
                     "figure.facecolor": SURFACE, "axes.facecolor": SURFACE})

MODELS = ["moss/fastlane-1", "anthropic/claude-haiku-5.5", "openai/gpt-6-luna", "google/gemini-3.5-flash-lite"]


def short(m):
    return m.split("/")[1]


def chart_ttft():
    main = S["main"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.6), sharex=True, sharey=True)
    xmax = max(main[k]["p95"] for k in main) * 1.62
    for ax, tag, title in [(axes[0], "plain", "No index (plain inference)"), (axes[1], "grounded", "Grounded (X-Moss-Indexes: moss-docs-md)")]:
        for i, m in enumerate(MODELS):
            d = main.get(f"{m}|{tag}")
            if not d:
                continue
            y = len(MODELS) - 1 - i
            col = ACCENT if m.startswith("moss/") else MUTED
            ax.plot([d["p50"], d["p95"]], [y, y], color=col, lw=2, solid_capstyle="butt", zorder=2)
            ax.plot([d["p95"], d["p95"]], [y - 0.16, y + 0.16], color=col, lw=2, zorder=2)
            ax.scatter([d["p50"]], [y], s=64, color=col, edgecolor=SURFACE, linewidth=2, zorder=3)
            cd = (S.get("control") or {}).get(f"{m}|{tag}")
            if cd:
                ax.scatter([cd["p50"]], [y], s=70, facecolor="none", edgecolor=col, linewidth=1.6, zorder=4)
            ax.text(d["p95"] + xmax * 0.015, y, f"p50 {d['p50']:.0f}  ·  p95 {d['p95']:.0f} ms", va="center", ha="left", fontsize=8.5, color=INK2)
        ax.axvline(200, color=INK2, lw=1, ls=(0, (4, 3)), zorder=1)
        ax.set_title(title, loc="left", fontsize=10.5, color=INK, pad=8)
        ax.set_yticks(range(len(MODELS)))
        ax.set_yticklabels([short(m) for m in MODELS][::-1], fontsize=9.5)
        ax.set_xlim(0, xmax)
        ax.set_ylim(-0.6, len(MODELS) - 0.4)
        ax.grid(axis="x", color=GRID, lw=0.8, zorder=0)
        ax.set_axisbelow(True)
        for sp in ("top", "right", "left"):
            ax.spines[sp].set_visible(False)
        ax.tick_params(axis="y", length=0)
        ax.set_xlabel("time to first token, ms (client-side)", fontsize=9)
    axes[1].text(208, len(MODELS) - 0.5, "Moss claim: < 200 ms", fontsize=8.5, color=INK2, va="top")
    axes[0].text(208, len(MODELS) - 0.5, "200 ms", fontsize=8.5, color=INK2, va="top")
    g = main.get("moss/fastlane-1|grounded"); p = main.get("moss/fastlane-1|plain")
    floor = (S.get("baseline") or {}).get("keepalive_p50")
    cg = (S.get("control") or {}).get("moss/fastlane-1|grounded")
    lo, hi = (min(g["p50"], cg["p50"]), max(g["p50"], cg["p50"])) if cg else (g["p50"], g["p50"])
    head = (f"Grounded moss/fastlane-1: median {lo:.0f}-{hi:.0f} ms to first token across two runs; retrieval itself ~{g['retrieve_p50']:.0f} ms" if cg
            else f"Grounded moss/fastlane-1: median {g['p50']:.0f} ms to first token (p95 {g['p95']:.0f} ms); retrieval itself ~{g['retrieve_p50']:.0f} ms")
    sub = (f"Dot = main-run median (n = {g['n_ok']} unique prompts per condition); ring = later control-run median (n = 40); whisker end = main-run p95."
           + (f" Network floor (GET /v1/models median): {floor:.0f} ms." if floor else ""))
    fig.text(0.012, 0.985, head, fontsize=12.5, fontweight="bold", color=INK, va="top")
    fig.text(0.012, 0.935, "\n".join(textwrap.wrap(sub, 132)), fontsize=8.8, color=INK2, va="top", linespacing=1.35)
    fig.subplots_adjust(top=0.78, bottom=0.14, left=0.14, right=0.985, wspace=0.08)
    out = os.path.join(RES, "ttft_by_model.png")
    fig.savefig(out, dpi=200)
    plt.close(fig)
    return out


def chart_promptsize():
    ps = S.get("promptsize")
    if not ps:
        return None
    pts = sorted((v["actual_tokens"], v["p50"]) for v in ps.values())
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    fig, ax = plt.subplots(figsize=(7.8, 4.4))
    ax.axhline(200, color=INK2, lw=1, ls=(0, (4, 3)), zorder=1)
    ax.text(xs[-1], 215, "200 ms: Moss's claim for grounded calls", fontsize=8.5, color=INK2, va="bottom", ha="right")
    ax.plot(xs, ys, color=ACCENT, lw=2, marker="o", ms=7, mec=SURFACE, mew=2, zorder=3)
    for x, y in zip(xs, ys):
        lab = f"{y/1000:.1f} s" if y >= 1000 else f"{y:.0f} ms"
        ax.annotate(lab, (x, y), xytext=(0, 11), textcoords="offset points", ha="center", fontsize=9, color=INK)
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xticks(xs)
    ax.set_xticklabels([f"{x/1000:.1f}k" if x >= 1000 else f"{x:.0f}" for x in xs])
    ax.set_yticks([100, 200, 500, 1000, 2000, 5000, 10000])
    ax.set_yticklabels(["0.1 s", "0.2 s", "0.5 s", "1 s", "2 s", "5 s", "10 s"])
    ax.set_ylim(120, 20000); ax.set_xlim(xs[0] * 0.7, xs[-1] * 1.5)
    ax.minorticks_off()
    ax.set_xlabel("prompt length, input tokens (log scale)", fontsize=9)
    ax.set_ylabel("median time to first token (log scale)", fontsize=9)
    ax.grid(axis="y", color=GRID, lw=0.8); ax.set_axisbelow(True)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    head = f"moss/fastlane-1: first token in {ys[0]/1000:.1f} s at {xs[0]:.0f} tokens, {ys[-2]/1000:.1f} s at {xs[-2]/1000:.0f}k, {ys[-1]/1000:.0f} s at {xs[-1]/1000:.0f}k"
    fig.text(0.012, 0.985, head, fontsize=11.5, fontweight="bold", va="top")
    fig.text(0.012, 0.925, "Unique random text, no index, n = 5 to 12 per point. p95 is within 21% of the median at every size (see latency_tables.md).", fontsize=8.5, color=INK2, va="top")
    fig.subplots_adjust(top=0.84, bottom=0.15, left=0.11, right=0.97)
    out = os.path.join(RES, "ttft_vs_prompt_length.png")
    fig.savefig(out, dpi=200)
    plt.close(fig)
    return out


if __name__ == "__main__":
    for f in (chart_ttft, chart_promptsize):
        try:
            print("wrote", f())
        except Exception as e:
            print(f.__name__, "failed:", type(e).__name__, e)
