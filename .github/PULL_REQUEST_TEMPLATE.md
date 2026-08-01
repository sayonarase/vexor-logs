## Vad & varfor
<!-- Kort beskrivning + lankad issue (Closes #NNN) och ev. ADR (docs/adr/ADR-XXXX). -->

## Definition of Done (Constitution v1.0)
- [ ] **Kod** foljer One Way Principle + Router->Service->Repository, kodgranser (fil<=800, funktion<=80, router<=25 endpoints, komponent<=500)
- [ ] **Tester** \u2014 unit -> integration -> e2e/Playwright dar tillampligt
- [ ] **Regressionsgrindar** \u2014 befintliga tester grona; ny regressionstest for det ratade
- [ ] **Docs** uppdaterade (help/README/ADR)
- [ ] **Changelog** \u2014 publik release-note i vexor-monitoring om anvandarsynligt
- [ ] **Lint** (ruff/eslint) rent
- [ ] **Typecheck** (mypy/tsc) rent
- [ ] **CI gron** pa alla workflows
- [ ] **A11y & Performance** \u2014 inga regressioner (a11y-labels, inga blockerande anrop i async-path)

## ADR
<!-- Kravs innan implementation for arbete > 1 dag. Lanka ADR eller motivera varfor ingen behovs. -->
