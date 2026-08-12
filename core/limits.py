"""How long a typed-in value may be. One place, because the numbers must agree.

Every text box on this site is rendered by a Form, so a cap declared here is a
cap on the whole site: there is no hand-written ``<input>`` in any template to
go around it. That is what makes this file sufficient rather than merely
well-meant, so it is held by a guard — ``test_no_hand_written_text_inputs`` in
core/tests.py fails if a template grows one.

⚠️ **Where the enforcement actually happens, stated exactly**, because the
   layers are not what the field declaration looks like:

   · ``CharField(max_length=…)`` — validated by the model *and* by every form.
   · ``TextField(max_length=…)`` — **not** validated by the model. Django 5.2's
     ``TextField.__init__`` appends no validator and the Postgres column stays
     ``text``. What it does do is pass ``max_length`` through
     ``TextField.formfield()`` into a ``forms.CharField``, which *is* validated
     and *does* render ``maxlength``. So every path a human types down — our
     pages and the admin, both of which go through a ModelForm — is covered,
     and ``bulk_create`` / a shell script is not. That is D9's standing caveat
     about ``save()`` not calling ``clean()``, arriving here as well; it is the
     reason these are called limits and not constraints.
   · A plain ``forms.Form`` field — validated by that form only. Which is why
     the numbers live here: ``NotifyForm.message`` writes
     ``EventNotification.message``, so the cap is declared **twice**, and a form
     that accepted more than its column expects is a 500 on submit rather than
     a sentence under the box. Two copies of a number is exactly the thing this
     module exists to prevent.

⚠️ The point is not to stop a determined attacker filling the disk — a rate
   limit does that (see accounts/views.py), and Postgres would take a gigabyte
   into a ``text`` column without complaining. The point is that somebody
   pasting a document into the notice box gets **a sentence under the box**
   instead of a 500, and that the columns behind the box keep holding what they
   were designed to hold.
"""

#: A paragraph or two, written by hand: an event's description, the body of a
#: notice, a note somebody leaves in the admin. Comfortably more than anybody
#: types into a web form, and far less than a pasted PDF.
LONG_TEXT = 2000

#: A line or two pinned to a single row — "bring gloves". Kept separate from
#: LONG_TEXT because these are read in a list, where a screenful in one cell
#: pushes every other row off the page.
SHORT_TEXT = 500

#: A search box. Short on purpose — nobody types a paragraph into one, and the
#: value goes straight into a `LIKE '%…%'` query, so there is no reason to accept
#: more than a person can plausibly be looking for.
SEARCH = 100

#: A phone number typed by hand, including punctuation and a country code.
#: PhoneNumberField parses its own; this is for the plain text boxes that only
#: get copied into a message (a guardian's number on the consent form).
PHONE = 32

#: An email address. The same 254 Django's own EmailField uses, so a form can
#: never accept an address that the column behind it will refuse — that
#: mismatch was a 500 on the ordinary registration path, not an exotic one.
EMAIL = 254

#: A password, or rather a passphrase. Long on purpose — a four-word phrase is
#: better than a mangled word, and nothing here should discourage one.
#:
#: ⚠️ It has an upper bound at all because hashing is **deliberately slow**:
#:    PBKDF2 over a megabyte of submitted "password" is CPU spent by us, on
#:    request, before anything is even stored. 128 is past every real passphrase
#:    and nowhere near enough to be worth sending.
PASSWORD = 128
