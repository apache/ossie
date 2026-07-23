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

## Summary

<!-- Describe what this PR does and why. -->

## Related Issues

<!-- Link any related GitHub issues: Closes #123, Fixes #456 -->

## Checklist

### Specification
- [ ] Spec changes are included in `core-spec/` and follow the existing structure
- [ ] Spec changes have been discussed on the mailing list or in a linked issue
- [ ] Breaking changes to the spec are clearly called out in the summary

### Ontology
- [ ] Ontology changes in `ontology/` are consistent with spec changes
- [ ] New or modified terms are defined and documented

### Converters
- [ ] Converter logic in `converters/` is updated to reflect spec or ontology changes
- [ ] New converters include tests under the converter's test directory

### Validation
- [ ] Validation rules in `validation/` are updated if the spec changed
- [ ] New validation cases are covered by tests

### Documentation
- [ ] `docs/` is updated to reflect any user-facing changes
- [ ] New features or behaviors are documented with examples where appropriate
- [ ] `CONTRIBUTING.md` is updated if the contribution process changed

### Examples
- [ ] `examples/` are added or updated for any new spec constructs or converter support

### Tests
- [ ] All existing tests pass (`pytest` / CI green)
- [ ] New functionality is covered by tests

### Compliance
- [ ] ASF license headers are present on all new source files
- [ ] No third-party dependencies are added without PMC/IPMC approval

### AI Tool Disclosure (Check the box below if used - Optional)
- [ ] This contribution used AI-assisted tooling (if yes, note tool/model below)

