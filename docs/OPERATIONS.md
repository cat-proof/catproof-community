# Local operations

## Back up

Stop writes before taking a coordinated backup. Keep `.env` and the database backup together in protected storage, because the database contains encrypted connector credentials and `.env` contains the encryption key. Never attach either to GitHub issues.

On a POSIX shell, from the installation directory:

```sh
umask 077
mkdir -p backups
docker compose stop app
docker compose exec -T db pg_dump -U catproof -d catproof -Fc > backups/registry.dump
cp .env backups/config.env
docker compose start app
```

Treat `registry.dump` and `config.env` as sensitive. Check the dump with `pg_restore --list` in a suitable PostgreSQL tool environment and periodically verify restoration in a separate disposable installation. This procedure is not a claim that your backup has been tested. Do not use legacy Windows PowerShell text redirection for binary database dumps; use a binary-preserving backup tool.

## Restore

Use an isolated installation with the same application version and preserved encryption key. Start only PostgreSQL, restore the dump with PostgreSQL's `pg_restore` tooling into an empty database, then start the application and verify records and credential readability. A restore overwrites or replaces data, so stop and back up any destination installation first. Never point a test restore at your working registry. Database/schema details are in [DATABASE.md](DATABASE.md).

## Upgrade

Read the target release notes, stop writers, and take a verified backup before changing versions. Edit `CATPROOF_IMAGE` in `.env` to the published version or digest you selected, then run `./community.sh start`. Do not regenerate `.env`. The launcher pulls the selected image; application startup applies migrations. Old and new writers must not overlap. Database migration rollback requires the old database backup and the matching old application version, not just a previous image tag.

For source builds, switch to a published source tag and use `./community.sh start --build`. Preserve `.env` and the existing project volumes.

## Remove

`./community.sh stop` removes containers and preserves volumes. To erase an installation deliberately, first export anything you need and then run `docker compose down -v`. This permanently removes the project's database and local CA volumes. Delete `.env` only after you no longer need to decrypt any associated backups. Remove the CatProof local CA from the trust store if you imported it. Revoke provider credentials at their provider when appropriate.
