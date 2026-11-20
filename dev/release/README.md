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

# Releasing Apache Ossie (incubating)

Apache Ossie releases are source releases made with
[Apache Trusted Releases (ATR)](https://releases.apache.org/). ATR holds the release
candidate while it is checked and voted on, then publishes the voted files unchanged to
[`dist/release/incubator/ossie`](https://dist.apache.org/repos/dist/release/incubator/ossie/),
which is served from `https://downloads.apache.org/incubator/ossie/`.

A release is three files:

- `apache-ossie-<version>-incubating-src.tar.gz`, the source archive
- `apache-ossie-<version>-incubating-src.tar.gz.asc`, the release manager's detached signature
- `apache-ossie-<version>-incubating-src.tar.gz.sha512`, its SHA-512 checksum

Because ATR publishes exactly the files that were voted on, file names never carry a release
candidate number. RC numbers exist only on git tags.

| Item | Example for 0.3.0 |
|------|-------------------|
| ATR release version | `0.3.0-incubating` |
| Version declared in the sources | `0.3.0` |
| Source archive | `apache-ossie-0.3.0-incubating-src.tar.gz` |
| Archive top-level directory | `apache-ossie-0.3.0-incubating-src/` |
| Release candidate tag | `apache-ossie-0.3.0-incubating-rc1` |
| Release tag | `apache-ossie-0.3.0-incubating` |
| Distribution directory | `https://downloads.apache.org/incubator/ossie/0.3.0-incubating/` |

The release is named `0.3.0-incubating`, as other podlings name theirs in ATR. The sources
declare plain `0.3.0`, for two reasons. Python package versions cannot contain
`-incubating`. The specification version is also written into every Ossie document, and
should not change when the project leaves the Incubator.

The tooling in this directory:

- [`build-source-release.sh`](build-source-release.sh) builds the source archive from a git ref
  with `git archive`, then signs it and writes its checksum.
- [`verify-release.sh`](verify-release.sh) verifies a candidate the way voters are expected to,
  and covers the checks ATR runs.

The `Source Release Check` workflow builds and verifies an unsigned archive on every pull
request, so `main` always produces an archive that passes these checks.

## One-time setup

### Release manager

1. **Signing key.** Use an OpenPGP key whose user ID carries an email address linked to your
   ASF account (your `@apache.org` address, or one verified at
   [id.apache.org](https://id.apache.org/)). ATR accepts new keys that are RSA of at least
   4096 bits, Ed25519, or ECDSA on P-256 or stronger. See
   [release signing](https://infra.apache.org/release-signing.html).
2. **Register the key in ATR.** Add your *public* key at
   [releases.apache.org/keys/add](https://releases.apache.org/keys/add) and associate it with
   Ossie. ATR verifies signatures only against keys held for the committee, and your key must
   be in Ossie's `KEYS` file before the release is published.
3. **SSH key (optional).** Uploading with rsync needs an SSH public key, added on the
   [ATR keys page](https://releases.apache.org/keys). This is separate from the signing key.
4. **Tools.** `git`, `gpg`, `curl`, Java 11+ (to run Apache RAT) and
   [`uv`](https://docs.astral.sh/uv/) (to run the tests). The
   [ATR client](https://github.com/apache/tooling-releases-client) (`atr`) is optional.

### Project (once, by a PPMC member)

On Ossie's committee page in ATR:

- **KEYS file management.** Ossie has no `KEYS` file in SVN yet. In ATR's default mode,
  *Automatically import changes to the KEYS file made in SVN*, the committee's keys are
  read-only in ATR and key uploads are refused. Switch to *Automatically update the
  committee's KEYS file*. ATR then creates and maintains
  `https://downloads.apache.org/incubator/ossie/KEYS` from the keys release managers upload.

On Ossie's project settings (release policy) in ATR:

- **Metadata → Repositories:** `https://github.com/apache/ossie` only. ATR then compares
  each source archive against the commit it was built from. If more than one repository is
  listed, ATR cannot record a single commit for the release. Each source archive must then be
  declared with its repository and commit under *Tools*, or ATR raises a concern.
- **Compose → Source artifact paths:** `*-src.tar.gz`. Leave the RAT exclusion fields empty,
  because the archive carries its own `.rat-excludes` at its root. ATR reads RAT exclusions
  only from there and scans only the folder that contains it.
- **Vote → Default vote recipient:** `dev@ossie.apache.org`, with a minimum voting period
  of 72 hours.
- **Finish → Default announce recipient:** `announce@apache.org`, with
  `dev@ossie.apache.org` in CC.
- **Finish → Announce release template:** the default describes the release as "a stable
  release available for production use". Reword it to suit the release, and keep the
  `{{PODLING_DISCLAIMER}}` placeholder.

ATR knows from ASF records that Ossie is a podling and applies the Incubator rules itself:
every artifact name must contain `incubating`, the source archive must have a `DISCLAIMER`
at its root, and every release is voted on twice.

## Making a release

### 1. Prepare

1. Start a `[DISCUSS]` thread on `dev@ossie.apache.org` to agree on the scope, the
   version and the release manager.
2. Make sure CI is green on the commit to release, including the `Source Release Check`
   workflow.

### 2. Set the release version

Open a pull request that replaces the development version (for example `0.2.0.dev0`) with
the release version without the `-incubating` suffix (`0.3.0`). `build-source-release.sh`
refuses to package a commit where any of these machine-readable versions differs from it:

- `python/pyproject.toml` (then run `uv lock` in `python/` to refresh `uv.lock`)
- `python/src/ossie/models.py`
- `core-spec/spec.yaml`
- `core-spec/ossie-schema.json`
- `ontology/ontology.json` and `ontology/mapping.json`

Update the human-readable references in the same pull request. `git grep -n 0.2.0.dev0`
lists them: the draft banner and version history in `core-spec/spec.md`, `docs/index.md`,
and the examples and compliance fixtures. Check that the copyright year in `NOTICE` is
the current year.

### 3. Tag the release candidate

Tag the merged commit and push the tag. ATR fetches the commit from GitHub to compare it
with the archive, so push the tag before you upload.

```sh
git tag -s apache-ossie-0.3.0-incubating-rc1 -m "Apache Ossie 0.3.0-incubating RC1" <commit>
git push apache apache-ossie-0.3.0-incubating-rc1
```

### 4. Build, sign and verify

```sh
dev/release/build-source-release.sh -r apache-ossie-0.3.0-incubating-rc1 0.3.0-incubating
```

This writes the three release files to `dist/0.3.0-incubating/` and prints the full commit
hash. To use a key other than gpg's default, pass `-u <key-id>`. Then run the same
verification voters will run:

```sh
gpg --armor --export <key-id> > dist/my-key.asc
dev/release/verify-release.sh -k dist/my-key.asc -g apache-ossie-0.3.0-incubating-rc1 -t dist/0.3.0-incubating 0.3.0-incubating
```

Pass `-k` while Ossie's `KEYS` file is not yet published. After that the default
(`https://downloads.apache.org/incubator/ossie/KEYS`) applies.

### 5. Compose the candidate in ATR

1. On [releases.apache.org](https://releases.apache.org/), open the Ossie project and start
   a release with version `0.3.0-incubating`.
2. Upload the three files from `dist/0.3.0-incubating/` using one of these routes:
   - **Browser:** the release's upload page. Enter the full 40-character commit hash printed
     by the build script as the source commit. If the project lists more than one
     repository, declare the archive's repository and commit under *Tools* instead.
   - **rsync:** the upload page shows the exact command, which has this shape:
     `rsync -av -e 'ssh -p 2222' dist/0.3.0-incubating/ <asf-uid>@releases.apache.org:/<project-key>/0.3.0-incubating/`.
     rsync does not carry a source commit. Record the commit afterwards, or the source
     comparison check raises a concern that must be noted before the vote.
   - **ATR client:** `atr upload` or `atr rsync`. See the
     [client guide](https://releases.apache.org/docs/client-guide).
3. Review the check results. *Blockers* must be fixed. *Concerns* must be acknowledged before
   a vote can start. The source comparison reports the archive as a valid subset of the
   GitHub checkout, because `.github/` and `.asf.yaml` are left out of it on purpose (see
   `.gitattributes`).

Every upload or deletion creates a new revision of the candidate, and the vote is held on a
single revision.

### 6. Vote, round 1: the PPMC on dev@

Start the vote from the compose page:

- Send round 1 to `dev@ossie.apache.org`, and choose `general@incubator.apache.org` as the
  round 2 list.
- Keep the vote open for at least 72 hours.
- Optionally tick *Automatically publish to SVN distribution area when this vote resolves*.
  This choice carries over to round 2.

Make sure the vote email names the RC tag and its commit and links to
[Verifying a release candidate](#verifying-a-release-candidate). If the default template
does not, customise the *Start vote template* in the project's Vote settings.

The vote passes with at least three binding +1 votes from the PPMC and more +1 than -1
votes. Check ATR's tally against the thread, then resolve the vote as *passed*. ATR starts
round 2 straight away, on the same revision and with the same duration.

### 7. Vote, round 2: the IPMC on general@

The Incubator PMC votes on `general@incubator.apache.org`. The vote passes with at least
three binding +1 votes from the IPMC and more +1 than -1 votes, after at least 72 hours.
ATR counts each round separately, so ask the mentors to vote on the `general@` thread too.
Resolve the vote as *passed* to move to the finish phase. ATR replies to both threads.

If either round fails or is cancelled, the candidate returns to the compose phase. Fix the
problem, tag `rc2`, rebuild, replace the files in ATR and start again from round 1.

### 8. Finish

1. **Publish.** If you did not choose automatic publication, publish from the finish page.
   The files land under `https://dist.apache.org/repos/dist/release/incubator/ossie/`.
   Check the download path shown on the finish page.
2. **Tag the release.** Point the release tag at the voted commit and push it:

   ```sh
   git tag -s apache-ossie-0.3.0-incubating "apache-ossie-0.3.0-incubating-rc1^{commit}" \
     -m "Apache Ossie 0.3.0-incubating"
   git push apache apache-ossie-0.3.0-incubating
   ```

3. **Website.** Add the release to the download page on
   [ossie.apache.org](https://ossie.apache.org/). Link the archive through
   `https://www.apache.org/dyn/closer.lua/incubator/ossie/...`, and link the `.asc`, the
   `.sha512` and `KEYS` from `https://downloads.apache.org/incubator/ossie/...`. Include
   instructions for verifying the download.
4. **Announce** through ATR. ATR sends the announcement only once the files have reached the
   download servers. Include the incubation disclaimer from [`DISCLAIMER`](../../DISCLAIMER)
   in the announcement.
5. **Python package (optional).** If the PPMC publishes `apache-ossie` to PyPI, build it from
   the release tag after the vote has passed, then record the distribution in ATR.

### 9. After the release

1. On `main`, move to the next development version (for example `0.4.0.dev0`) in the files
   listed in step 2.
2. Record the release in `core-spec/spec.md` (version history) and `docs/index.md` (latest
   released version).
3. Once a newer release supersedes this one, archive it in ATR (*Archive release* on its
   release page). Archived releases stay available from `archive.apache.org`.

## Verifying a release candidate

Download the candidate's files from the ATR page linked in the vote email into an empty
directory. Then, from a clone of `apache/ossie` checked out at a trusted commit:

```sh
git fetch apache tag apache-ossie-0.3.0-incubating-rc1
dev/release/verify-release.sh -g apache-ossie-0.3.0-incubating-rc1 -t <download-dir> 0.3.0-incubating
```

The script:

- verifies the SHA-512 checksum, and the signature against Ossie's published `KEYS`;
- checks that the archive has a single `apache-ossie-<version>-incubating-src/` directory
  with `LICENSE`, `NOTICE` and `DISCLAIMER`, and no symbolic links;
- reports binary files other than images, since a source release must not contain compiled
  code;
- runs Apache RAT with the archive's own `.rat-excludes`, as ATR does;
- with `-g`, checks that the archive contains exactly the files of the RC tag;
- with `-t`, runs the Python model and validation test suites from the extracted archive.

After the signature check passes, the RAT and test steps run code from the archive.

Running the script is not a substitute for reviewing the candidate. Read `LICENSE` and
`NOTICE`, look for bundled third-party material, and try the specification and tooling. Then
vote on the list, or in ATR if the vote uses Trusted mode.

## Trusted Publishing

ATR can also accept uploads straight from a GitHub Actions workflow, through
`apache/tooling-actions/upload-to-atr`, with no long-lived credential. This needs:

- approval from the ASF Security team;
- reproducible builds, rebuilt on trusted hardware before the vote completes;
- an Infra-issued *Automated Release Signing* key, held as a repository secret;
- the workflow paths registered in the project's Trusted Publishing settings in ATR.

The source archive is built with `git archive` from a commit, so repeated builds of the same
commit on the same machine produce identical bytes. This is a starting point for that
process. See [Trusted Publishing](https://releases.apache.org/docs/trusted-publishing).
