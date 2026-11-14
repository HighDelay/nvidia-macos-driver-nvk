# Contributing

Bug reports and pull requests are welcome. Please read this first; it keeps the queue small enough that every PR
gets a real review.

## Reporting a bug

1. Press Send logs in the 1401 app (Windows or Mac). It gives you a report ID like `NM-ABCD1234`.
2. Open an issue with the bug form and put the report ID in it.
3. Describe what you see in a few sentences. The logs are in the report, so don't paste them in.

One problem per issue. If you found the cause, say how you know: what you ran and what it printed.

AI-assisted contributions are welcome. Review and understand what you submit, and be ready to explain your
changes and answer review questions.

## Pull requests

- One change per PR. A fix, a feature or a cleanup, not all three. Small PRs get merged; 2,000-line PRs sit.
- Say what you tested. Your card, macOS version, and what you did to check it. "Builds" is not tested.
- Keep the description short: what was wrong, what you changed, how you tested it. Link the issue.
- Commit messages: `component: short summary` under 72 characters (`NVRMFB: fix 165 Hz mode missing`), then a
  plain explanation if one is needed.
- Comments in code explain why the code does what it does. They are not a log of when you found something.
- Sign off every commit (`git commit -s`). It certifies you wrote the change or may submit it under this
  repository's licence (Developer Certificate of Origin, <https://developercertificate.org>).

Kernel changes (`kexts/`) need component compilation and an isolated kernel-collection
check. Record actual boot results separately, including the GPU and macOS version;
a source merge or link check does not qualify deployment. Retain recovery files
and obtain the required local testing and release approval before publication.

## Where help is needed

See the issues labelled `help wanted`. The areas with the most open problems:

- Turing (GTX 16, RTX 20): flicker and black screens on cards with a small BAR.
- Display: ultrawide and high refresh rate modes, hot-plugging a monitor.
- Video: hardware decode in sandboxed processes (Chrome, Photos).
- Laptops: machines where the screen is wired to the Intel GPU.

If you own a card we haven't tested, a working report (the "It works" form) is as useful as code.

## Building

See [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md) for the layout of the source and how each part is built.
Setting up, the tests and the rules for changes are in [DEVELOPING.md](DEVELOPING.md).
