---
name: Bug report
about: Something downloaded wrongly, or a command misbehaved
title: ""
labels: bug
---

**Version and platform**

<!-- `acadfetch --version`, `python -VV`, OS and architecture -->

**What you ran**

<!-- the exact command, with tokens replaced by xxxxx -->

**What happened / what you expected**

**Is it reproducible offline?**

- [ ] Yes, with the local fixture path — steps:
- [ ] No, only against the live repository

**Is this about bytes on disk?**

If a file is wrong, this is the important part:

- [ ] `acadfetch verify` on the result says `MISMATCH` (paste the output)
- [ ] A `.part` file or `.part.meta.json` was left behind (say which)
- [ ] The repository publishes a checksum for the file

**Dry run output**

<!-- `acadfetch fetch <target> -n` — this is metadata only, no download -->

**Network path**

<!-- any proxy, VPN, region, or a WAF answering 403. figshare's API blocks some
     networks outright; say if you are behind one -->

**Anything else**
