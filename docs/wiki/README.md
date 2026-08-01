# Wiki source

`Home.md` is the source for the repository wiki page at
<https://github.com/roguen/bookly-demo/wiki>.

It is tracked here rather than only in the wiki because GitHub's wiki is a
separate git repository that cannot be created through the API — the first
page has to be made in the web UI. Keeping the source in the main repo means
the page is reviewable in a pull request and recoverable if the wiki is ever
reset.

To publish an update once the wiki exists:

```bash
git clone https://github.com/roguen/bookly-demo.wiki.git /tmp/bookly-wiki
cp docs/wiki/Home.md /tmp/bookly-wiki/Home.md
cd /tmp/bookly-wiki && git add -A && git commit -m "Update project overview" && git push
```
