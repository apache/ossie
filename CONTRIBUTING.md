<!--
  Licensed to the Apache Software Foundation (ASF) under one
  or more contributor license agreements.  See the NOTICE file
  distributed with this work for additional information
  regarding copyright ownership.  The ASF licenses this file
  to you under the Apache License, Version 2.0 (the
  "License"); you may not use this file except in compliance
  with the License.  You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing,
  software distributed under the License is distributed on an
  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
  KIND, either express or implied.  See the License for the
  specific language governing permissions and limitations
  under the License.
-->

# Contributing to Apache Ossie (incubating)

Thank you for your interest in contributing to Apache Ossie! We welcome
contributions from everyone — whether you are a developer, a data engineer, a BI
analyst, or simply someone interested in the future of semantic interoperability.

Apache Ossie was formerly known as **Open Semantic Interchange (OSI)**.

> **Apache Ossie is an effort undergoing incubation at the Apache Software
> Foundation (ASF), sponsored by the Apache Incubator.** Incubation is required
> of all newly accepted projects until a further review indicates that the
> infrastructure, communications, and decision making process have stabilized in
> a manner consistent with other successful ASF projects. See the
> [DISCLAIMER](DISCLAIMER) for details.

Apache Ossie is governed by [The Apache Way](https://www.apache.org/theapacheway/),
the ASF's collection of principles and practices for building open, vendor-neutral
communities. If you are new to the ASF, the
[Apache Incubator](https://incubator.apache.org/) and the
[ASF New Committers guide](https://www.apache.org/dev/new-committers-guide.html)
are good starting points.

## Ways to Contribute

- **Specification Feedback**: Review proposed specification changes and share your perspective on the mailing list, GitHub pull requests, and issues.
- **Use Case Discussions**: Share how your organization uses semantic models and what challenges you face — this helps shape the specification to address real-world needs.
- **Code Contributions**: Contribute to validation tooling, converters, examples, or any other part of the project.
- **Documentation**: Help improve and expand the project documentation.
- **Community Support**: Answer questions, participate in discussions, and help onboard new contributors.

## Communication

At the ASF, **the mailing lists are the primary channel** for the project. Consensus
is built and decisions are recorded on the lists, so please bring discussions there.
A guiding principle of the Apache Way is: *if it didn't happen on the mailing list,
it didn't happen.*

- **dev@ossie.apache.org** — development and community discussion. Subscribe by
  emailing `dev-subscribe@ossie.apache.org`, then browse the
  [archives](https://lists.apache.org/list.html?dev@ossie.apache.org).
- **commits@ossie.apache.org** — automated notifications for commits, pull requests.
  Subscribe via `commits-subscribe@ossie.apache.org`.
- **issues@ossie.apache.org** — automated notifications for issues.
  Subscribe via `issues-subscribe@ossie.apache.org`.
- **private@ossie.apache.org** — the Podling Project Management Committee (PPMC)
  private list, used only for confidential matters such as committer nominations.

Secondary, less formal channels (never a substitute for the lists when a decision
is being made):

- [GitHub Issues](https://github.com/apache/ossie/issues) and
  [Discussions](https://github.com/apache/ossie/discussions)
- [Slack](https://join.slack.com/t/apache-ossie/shared_invite/zt-42zw4rflt-Gpve8_NFJq7AsdAQTY~SCg)

## Getting Started

1. **Subscribe to the dev list**: Email `dev-subscribe@ossie.apache.org` and introduce yourself.
2. **Read the Specification**: Familiarize yourself with the [core specification](core-spec/spec.md) to understand the semantic model format.
3. **Explore the Examples**: Review the [TPC-DS example](examples/tpcds_semantic_model.yaml) to see a complete semantic model in practice.
4. **Find something to work on**: Browse the [issues](https://github.com/apache/ossie/issues), or raise a topic on the dev list.
5. **Submit a Pull Request**: Fork the repository, make your changes, and submit a pull request. All contributions go through the review process described below.

## Contributor License Agreement (ICLA)

All contributions to Apache Ossie are made under the [Apache License 2.0](LICENSE).
By submitting a pull request or patch, you agree that your contribution is licensed
under those terms (see Section 5 of the license).

Before your first non-trivial contribution can be merged, and always before you are
granted commit access, you must have an
[Individual Contributor License Agreement (ICLA)](https://www.apache.org/licenses/contributor-agreements.html#clas)
on file with the ASF. If you are contributing on behalf of your employer, a
[Corporate CLA (CCLA)](https://www.apache.org/licenses/contributor-agreements.html#clas)
may also be required. Please keep individual commits signed off and attributed to
the correct author so that provenance is clear.

## AI-Assisted Contributions

We welcome contributions that use AI-assisted tooling (GitHub Copilot, Claude,
ChatGPT, etc.). Per the
[ASF Generative Tooling Guidance](https://www.apache.org/legal/generative-tooling.html):

- You may use any AI tool provided the output does not include third-party
  copyrighted material under an incompatible license
- You remain personally responsible for all code you submit, regardless of
  how it was produced
- Please disclose AI tool usage in your PR description or commit message
  (e.g., `Co-authored-by: Copilot <copilot@github.com>` or a note in the PR)

This is not a gate — it is a transparency practice that helps the community
understand how contributions are produced.

## Contribution Workflow

The project's canonical repository is hosted at
[github.com/apache/ossie](https://github.com/apache/ossie), mirrored from ASF
infrastructure (GitBox).

### Code, Documentation, and Tooling

Non-specification contributions (bug fixes, tooling, documentation, converters,
examples) follow standard GitHub pull request review:

1. Open an issue or start a thread on `dev@` for anything non-trivial, so the
   approach can be discussed before significant work begins.
2. Fork the repository and create a topic branch for your change.
3. Submit a pull request with a clear description of the motivation and the change.
4. A committer reviews and merges once the change has at least one **+1** from a
   committer and no unresolved **-1**. The project follows a review-then-commit (RTC)
   model: changes are merged after review rather than committed first.

### Specification Changes

Changes to the Apache Ossie specification carry a higher bar and follow a structured
process:

1. **Proposal**: Announce the proposal on `dev@ossie.apache.org` and open a GitHub
   pull request with a clear description of the motivation, the change itself, and
   its impact on existing implementations.
2. **Discussion period**: The community has a minimum of 7 days (72 hours for a
   formal `[VOTE]`) to review and discuss. Complex changes may require a longer
   review window.
3. **Vote**: Once discussion has settled, a `[VOTE]` thread is called on the dev
   list. See [Decision Making and Voting](#decision-making-and-voting) below.

## Decision Making and Voting

Apache Ossie strives for **lazy consensus** — proceeding when no one objects — and
falls back to a formal vote when consensus cannot be reached. Votes happen on
`dev@ossie.apache.org` so they are publicly recorded. Voters express:

- **+1**: In favor.
- **0**: Abstain / no strong opinion.
- **-1**: Against. For code and specification changes this is a **veto** and
  **must** be accompanied by a technical justification; a valid veto can only be
  resolved by addressing the stated concern.

Conventions:

- **Code and other changes** pass by lazy consensus: at least one binding **+1** and
  no vetoes.
- **Specification changes** require at least three binding **+1** votes and no
  vetoes.
- **Committer and PPMC nominations** are held on the private list and pass by lazy
  consensus (no **-1** within 72 hours).
- **Procedural votes** (e.g. adopting a policy) pass by simple majority and cannot
  be vetoed.

**Binding votes** on the podling are cast by PPMC members. Everyone is encouraged to
vote; non-binding votes are valued input that informs the outcome.

### Releases

As an incubating project, every release is approved in two stages:

1. A `[VOTE]` on `dev@ossie.apache.org` that passes with at least **three binding
   +1 votes** from the PPMC and more +1 than -1 votes.
2. A second `[VOTE]` on `general@incubator.apache.org` that passes with at least
   three binding +1 votes from the Incubator PMC (IPMC).

Releases are source releases distributed through official ASF channels and must
comply with [ASF release policy](https://www.apache.org/legal/release-policy.html).

## Community Values — The Apache Way

Apache Ossie follows [The Apache Way](https://www.apache.org/theapacheway/), built
on these principles:

- **Community over code**: A healthy, welcoming community is the project's most
  important asset.
- **Meritocracy**: Merit is based on contribution and never expires. Any constructive
  contribution earns merit — code, documentation, testing, community support, and
  specification reviews all count.
- **Peer-based**: Every participant is treated as a peer regardless of employer
  affiliation or seniority. Decisions are made by the community, not by any single
  company or individual.
- **Consensus decision making**: We strive for consensus in all decisions. When full
  consensus cannot be reached, a formal vote may be called.
- **Open Communications**: Technical discussions, design decisions, and specification
  changes happen in the open on the mailing lists and other public resources.
  Decisions are made asynchronously so contributors in every timezone can take part.
- **Responsible oversight**: The community collectively ensures the specification and
  tooling remain high quality, secure, and aligned with the project's mission.
- **Vendor neutrality**: The project operates independently of any single vendor or
  organization.

## Roles and Responsibilities

### Contributors

Anyone who contributes to the project in any form — code, documentation, bug reports,
specification feedback, or community support. Contributors are encouraged to
participate in all discussions and votes; contributor votes are non-binding but valued.

### Committers

Contributors who have earned write access to the repository through sustained,
high-quality contributions. Committers review and merge pull requests and have
binding votes on the project's technical decisions. All committers must have an ICLA
on file.

### Podling Project Management Committee (PPMC)

The PPMC is responsible for the overall direction and health of the podling —
technical direction, community growth, and oversight of releases. PPMC members'
votes are binding. During incubation, PPMC members work alongside the project's
mentors and, upon graduation, the PPMC becomes the project's PMC.

### Mentors

Experienced ASF members assigned by the Incubator to guide the podling through the
incubation process. Mentors are PPMC members who help the community learn the Apache
Way, and they shepherd release votes to the Incubator PMC.

### Incubator PMC (IPMC)

The [Apache Incubator](https://incubator.apache.org/) PMC provides oversight for all
podlings, including approving releases and reviewing quarterly podling reports until
the project graduates to a top-level project.

## Becoming a Committer

Committership is earned through sustained contribution to the project. There is no
fixed formula — the community recognizes contributors who demonstrate consistent,
high-quality involvement over time.

### What counts

Committer candidacy is based on the breadth and quality of your contributions across
any of these areas:

- Code contributions (converters, validation tooling, examples, tests)
- Specification feedback and review participation
- Documentation improvements
- Community support (answering questions, helping onboard others, discussion on the lists)
- Working group participation

### The process

1. Any PPMC member may nominate a contributor by starting a discussion on the private
   list (`private@ossie.apache.org`).
2. The nomination includes a summary of the candidate's contributions and a rationale
   for committer status.
3. The PPMC discusses and votes. A nomination passes by lazy consensus — if no **-1**
   votes are received within 72 hours.
4. If the vote passes, the candidate is invited to become a committer. Once they
   accept and their ICLA is on file, their Apache account is set up and write access
   is granted.
5. The new committer is announced to the community and listed in the project
   documentation.

There is no minimum time requirement. What matters is the quality, consistency, and
impact of your contributions. Contributors who show sound judgment and help grow the
community may also be invited to join the PPMC.

## Code of Conduct

All participants in the Apache Ossie community are expected to follow the
[Apache Software Foundation Code of Conduct](https://www.apache.org/foundation/policies/conduct).
We are committed to providing a welcoming and inclusive environment for everyone,
regardless of background or experience level. Concerns may be raised privately with
the PPMC (`private@ossie.apache.org`) or, for foundation-level matters, with the ASF
per the linked policy.

## Trademarks

Apache Ossie, Ossie, Apache, the Apache feather logo, and the Apache Ossie project
logo are trademarks of The Apache Software Foundation. Please review the
[ASF trademark policy](https://www.apache.org/foundation/marks/) before using any of
these marks.

## License

All content in this repository — including code, specification, and documentation —
is licensed under the [Apache License 2.0](LICENSE).

By submitting a contribution, you agree that your contribution will be licensed under
the same terms, as described in the [Contributor License Agreement](#contributor-license-agreement-icla)
section above.
