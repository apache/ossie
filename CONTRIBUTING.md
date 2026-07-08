# Contributing to Open Semantic Interchange (OSI)

> [!IMPORTANT]
> **This repository is legacy and will be archived.** The project has moved to [apache/ossie](https://github.com/apache/ossie). Please direct new contributions there.

We welcome contributions from everyone — whether you are a developer, a data engineer, a BI analyst, or simply someone interested in the future of semantic interoperability.

## Ways to Contribute

- **Specification Feedback**: Review proposed specification changes and share your perspective on GitHub pull requests and issues.
- **Use Case Discussions**: Share how your organization uses semantic models and what challenges you face — this helps shape the specification to address real-world needs.
- **Code Contributions**: Contribute to validation tooling, converters, examples, or any other part of the project.
- **Documentation**: Help improve and expand the project documentation.
- **Community Support**: Answer questions, participate in discussions, and help onboard new contributors.

## Getting Started

1. **Read the Specification**: Familiarize yourself with the [core specification](core-spec/spec.md) to understand the semantic model format.
2. **Explore the Examples**: Review the [TPC-DS example](examples/tpcds_semantic_model.yaml) to see a complete semantic model in practice.
3. **Join the Conversation**: Open or participate in [GitHub Issues](https://github.com/open-semantic-interchange) and Discussions to share ideas and feedback.
4. **Submit a Pull Request**: Fork the repository, make your changes, and submit a pull request. All contributions go through the standard review process described below.
5. **Join the Slack workspace**: Chat directly with the community by [joining Slack](https://join.slack.com/t/apache-ossie/shared_invite/zt-42i1xkgy8-7YQtKEDq7v~mceFmdiLhkA).

## Contribution Guidelines

### Specification Changes

Changes to the OSI Specification follow a structured process:

1. **Proposal**: Submit a GitHub pull request with a clear description of the motivation, the change itself, and its impact on existing implementations.
2. **Discussion period**: The community has a minimum of 7 days to review and discuss the proposed change. Complex changes may require a longer review window.
3. **Vote**: Once the discussion period has elapsed, a vote is called. TSC members and committers cast votes:
   - **+1** (Yes): In favor of the change
   - **0** (Abstain): No opinion or willing to go with the majority
   - **-1** (Veto): Against the change — must be accompanied by a technical justification

   TSC member votes are binding. A specification change passes with at least 2 binding +1 votes and no vetoes. A veto can only be overridden by addressing the stated concern or by a supermajority (two-thirds) vote of the TSC.

### All Other Contributions

Non-specification contributions (bug fixes, tooling, documentation, converters) go through standard GitHub pull request review. Committers review and merge these contributions.

## Community Values

OSI follows the Apache Way, built on these principles:

- **Meritocracy**: Merit is based on contribution and never expires. Any constructive contribution earns merit — code, documentation, testing, community support, and specification reviews all count equally.
- **Peer-based**: Every participant is treated as a peer regardless of employer affiliation or seniority. Decisions are made by the community, not by any single company or individual.
- **Consensus decision making**: We strive for consensus in all decisions. When full consensus cannot be reached, a formal vote may be called.
- **Open Communications**: All technical discussions, design decisions, and specification changes happen in the open — on GitHub issues, pull requests, and discussions. If it didn't happen on a public resource, it didn't happen.
- **Responsible oversight**: The community collectively ensures the specification and tooling remain high quality, secure, and aligned with the project's mission.
- **Independence**: The project operates independently of any single vendor or organization.

## Roles and Responsibilities

### Contributors

Anyone who contributes to the project in any form — code, documentation, bug reports, specification feedback, or community support. Contributors do not have binding vote rights but are encouraged to participate in all discussions.

### Specification Reviewers

Community members with domain expertise (e.g., in BI, AI, data engineering) who review proposed specification changes for correctness, completeness, and practical applicability.

### Committers

Contributors who have earned write access to the repository through sustained, high-quality contributions. Committers can merge pull requests and have binding votes on non-specification matters.

### Technical Steering Committee (TSC)

The TSC is responsible for the overall technical direction of the OSI Specification. TSC members have binding votes on specification changes. New TSC members are elected by existing TSC members.

## Becoming a Committer

Committership is earned through sustained contribution to the project. There is no fixed formula — the community recognizes contributors who demonstrate consistent, high-quality involvement over time.

### What counts

Committer candidacy is based on the breadth and quality of your contributions across any of these areas:

- Code contributions (converters, validation tooling, examples, tests)
- Specification feedback and review participation
- Documentation improvements
- Community support (answering questions, helping onboard others)
- Working group participation

### The process

1. Any existing committer or TSC member may nominate a contributor by opening a discussion (GitHub Discussions or the project mailing list).
2. The nomination includes a summary of the candidate's contributions and a rationale for committer status.
3. Existing committers and TSC members discuss the nomination. The standard consensus voting convention applies (+1 / 0 / -1).
4. A nomination passes with lazy consensus — if no -1 votes are received within 72 hours, the candidate is welcomed as a committer.
5. The new committer is added to the repository with write access and listed in the project documentation.

There is no minimum time requirement. What matters is the quality, consistency, and impact of your contributions. A focused burst of high-value work can carry as much weight as a longer track record.

## Code of Conduct

All participants in the OSI community are expected to treat each other with respect and professionalism. We are committed to providing a welcoming and inclusive environment for everyone, regardless of background or experience level.

## License

All content in this repository — including code, specification, and documentation — is licensed under the [Apache License 2.0](LICENSE).

By submitting a contribution, you agree that your contribution will be licensed under the same terms.
