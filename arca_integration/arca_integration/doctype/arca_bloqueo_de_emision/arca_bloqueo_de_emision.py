"""One emission at a time per company, held in a row anyone can see.

A ``SELECT ... FOR UPDATE`` cannot do this job: the row it locks is released by
the ``commit`` that stores the assigned number, and there is no row to lock at
all when no other comprobante is live. So the lock is a document whose *name* is
the key: two processes cannot both create it, and the row survives the commit
that the emission needs.

The controller only protects the row from a casual edit. The engine creates and
deletes it with ``ignore_permissions``.
"""

import frappe
from frappe.model.document import Document


class ARCABloqueodeEmision(Document):
    def validate(self):
        if self.is_new():
            return

        frappe.throw(
            "Un bloqueo de emisión no se edita: describe una emisión que está "
            "ocurriendo. Si quedó tomado por un proceso que murió, esperá a que "
            "venza o borralo desde la lista."
        )
