"""bench commands of this app: `bench --site <site> arca-demo` and `arca-tickets`."""

import click
from frappe.commands import get_site, pass_context


def _path(value: str | None) -> str | None:
	"""Bench runs commands from sites/: accept paths relative to the bench folder too."""
	from pathlib import Path

	if value is None:
		return None
	for candidate in (Path(value), Path("..") / value):
		if candidate.is_file():
			return str(candidate.resolve())
	raise click.BadParameter(f"{value} does not exist")


def _connect(context):
	import frappe

	site = get_site(context)
	frappe.init(site)
	frappe.connect()
	return frappe


@click.command("arca-demo")
@click.option(
	"--cert", required=True, type=click.Path(dir_okay=False), help="Homologation certificate (.crt)"
)
@click.option("--key", required=True, type=click.Path(dir_okay=False), help="Its private key (.key)")
@click.option("--company", default="Demo ARCA", show_default=True)
@click.option("--abbr", default="DARCA", show_default=True)
@click.option("--tickets", type=click.Path(dir_okay=False), help="File saved with 'arca-tickets export'")
@click.option("--no-invoices", is_flag=True, help="Do not create draft invoices")
@click.option("--force", is_flag=True, help="Run even if other companies have ARCA invoices")
@pass_context
def arca_demo(context, cert, key, company, abbr, tickets, no_invoices, force):
	"""Set up a site with demo data to issue test invoices in ARCA homologation."""
	cert, key, tickets = _path(cert), _path(key), _path(tickets)
	frappe = _connect(context)
	from arca_integration.demo import DemoError, setup_demo

	try:
		for line in setup_demo(
			cert, key, company, abbr, force=force, invoices=not no_invoices, tickets=tickets
		):
			click.echo(line)
	except DemoError as exc:
		frappe.db.commit()  # keep what was set up, e.g. to fix the connection from the UI
		raise click.ClickException(str(exc)) from exc
	finally:
		frappe.destroy()


@click.command("arca-tickets")
@click.argument("action", type=click.Choice(["export", "import"]))
@click.argument("path", type=click.Path(dir_okay=False))
@pass_context
def arca_tickets(context, action, path):
	"""Save or restore valid WSAA tickets around a reinstall (the file holds credentials: delete it after use)."""
	if action == "import":
		path = _path(path)
	elif not path.startswith("/"):
		path = str((__import__("pathlib").Path("..") / path).resolve())
	frappe = _connect(context)
	from arca_integration.demo import export_tickets, import_tickets

	try:
		if action == "export":
			click.echo(f"Tickets saved: {export_tickets(path)} -> {path}")
		else:
			click.echo(f"Tickets restored: {import_tickets(path)}")
			frappe.db.commit()
	finally:
		frappe.destroy()


commands = [arca_demo, arca_tickets]
