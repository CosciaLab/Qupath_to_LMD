"""The experiment at a glance, in the sidebar, updated as each stage completes.

One line says what the experiment is — slides, samples, collectors — and a checklist says how far it
has got. It stays in view while the page scrolls, so whatever the user changes, they can see what
it did to the experiment (`decisions.md` 076).
"""

from dataclasses import dataclass, field

import streamlit as st

from qupath_to_lmd import plate

STAGES = ("Slides", "Samples", "Collector", "Cut")
EMOJI = {"Slides": "🔬", "Samples": "🧬", "Collector": "🧫", "Cut": "✂️"}


@dataclass
class Summary:
    """Fills a sidebar placeholder; each stage reports in as it finishes."""

    placeholder: object = None
    lines: dict[str, str] = field(default_factory=dict)
    figures: dict[str, str] = field(default_factory=dict)

    @classmethod
    def start(cls) -> "Summary":
        """An empty summary bound to the sidebar."""
        st.sidebar.markdown("### Your experiment")
        summary = cls(placeholder=st.sidebar.empty())
        summary._render()
        return summary

    def slides(self, context) -> None:
        """Stage 1 is done."""
        n = len(context.slides)
        shapes = sum(slide.report.n_shapes_kept for slide in context.slides)
        self.figures["slides"] = f"**{n} slide{'s' if n != 1 else ''}**"
        self.lines["Slides"] = f"{n} slide{'s' if n != 1 else ''}, {shapes:,} shapes, calibrated"
        self._render()

    def samples(self, sample_set, label: str) -> None:
        """Stage 2 is done."""
        n = len(sample_set.samples)
        classes = {sample.rpartition("_r")[0] or sample for sample in sample_set.samples}
        detail = f"{n} samples"
        if sample_set.workflow != "legacy" and classes:
            detail += f" ({len(classes)} classes × replicates)"
        self.figures["samples"] = f"**{n} samples**"
        self.lines["Samples"] = f"{label.split(' —')[0]}: {detail}, {sample_set.n_collected():,} shapes to cut"
        self._render()

    def plates(self, layout, n_samples: int) -> None:
        """Stage 3 is done."""
        chosen = plate.collector(layout.plate_type)
        n = layout.n_plates
        placed = len(layout.assignment)
        self.figures["plates"] = f"**{n} {chosen.noun}{'s' if n != 1 else ''}**"
        self.lines["Collector"] = f"{placed} of {n_samples} samples placed on {n} × {chosen.label}"
        self._render()

    def cut(self, ready: bool) -> None:
        """Stage 4: whether a current download exists."""
        self.lines["Cut"] = "ready to download" if ready else "not processed yet"
        if not ready:
            self.lines.pop("Cut")
        self._render()

    def forget(self, *stages: str) -> None:
        """Withdraw stages about to report again.

        A fragment rerun reuses this summary, so a stage that now stops early would otherwise
        leave its last line showing.
        """
        for stage in stages:
            self.lines.pop(stage, None)
        if "Collector" in stages:
            self.figures.pop("plates", None)
        self._render()

    def _render(self) -> None:
        if self.placeholder is None:
            return
        with self.placeholder.container():
            figures = [self.figures[key] for key in ("slides", "samples", "plates") if key in self.figures]
            st.markdown(" → ".join(figures) if figures else "Upload a QuPath export to begin.")
            for stage in STAGES:
                if stage in self.lines:
                    st.markdown(f"{EMOJI[stage]} **{stage}** ✅ — {self.lines[stage]}")
                else:
                    st.markdown(f":gray[{EMOJI[stage]} {stage}]")
