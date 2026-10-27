# Evidence Desk

**Reconstruct an event from a series of available sources.** Here "event" doesn't necessarily mean a single event. It is an umbrella term for a long-running task made up of many smaller tasks that happen in parallel, in sequence, or at no particular interval.

Examples of this can be seen in many settings (these are actual problems I discussed with real people):

1. Went on a trip last month and needed to sort out smaller details about costs and transactions made spontaneously during the trip. These weren't organised in one place; they were spread across several chats and emails.
2. Travelling to conferences involves many parties, from booking to planning and orchestrating different activities, while the situation keeps changing. This leads to conflicts between the planned itinerary and what actually took place.
3. In larger companies, buying goods follows a process called a Service Order Request. Several parties communicate to find the best goods at the best price while following legal procedure, so a trail must be kept. But the communication rarely happens in one medium.

## The idea

- In all the above cases, we have heaps of data in the form of emails, chats, PDFs, Excel files and so on. These are all different pieces of evidence centred around a certain topic or person.
- We turn that pile into a timeline you can check. It extracts events, ties them to source documents, works out the dates in a fixed, methodical way, and then finds the gaps and conflicts between the evidence and the planned tasks, to understand how the entire event unfolded.
- In the deployed page, an entire conference journey is presented. It contains the following kinds of data:
  - `.eml` files from Outlook
  - `.mbox` files from Google Takeout (bulk mail)
  - `.txt` files from WhatsApp chats
  - PDF files from mail attachments, registrations and confirmations
  - Images, including screenshots

## How it works

### Preprocessing

- The data is preprocessed systematically and ingested. This involves understanding how temporal data such as dates is handled, while also removing waste content such as email headers, so that garbage doesn't get through and hallucinations don't propagate forward.
- We handle the following problems during preprocessing:
  - Eliminating duplicates from both chats and emails. This covers the same email shared across different email IDs, as well as emails that have both email IDs as recipients.
  - An email thread may be missing its original emails. These get flagged.
  - Lost chat messages that slipped through but are referenced in newer messages get flagged.
  - Attachments mentioned in emails but not uploaded.
  - Attachments present only as placeholders in chat backups.
  - Timezone mismatches in chats, from messaging between international parties.
  - And multiple other aspects.

### Extraction

- The data is then sent to an external LLM, but only after sensitive details like passwords and card numbers are masked. The model has one job here: read the text and pull out events, who was involved, where, and what the text says about time. Along with every event, it must return the exact sentence it got it from.
- Every quote the model returns is searched for again in the original source. If the quote can't be found, it is dropped, and an event with no quote left is flagged for a human to check. This way every event on the timeline points to a real line in a real document.
- Dates are never worked out by the model. It only reports what the text says ("yesterday", "next Friday", "16-07-2026"), and our code converts that into an actual date based on when the message was sent. The same input always gives the same date.
- Some data doesn't need a model at all. Itineraries made as tables (Excel sheets or screenshots of a schedule) are read directly by code, row by row.

### People and the graph

- The same person often appears under different names: a full name in email, a nickname in a phone contact, a different address for work. We only merge people automatically when they share an email address or phone number. Everything else is suggested, and the user decides.
- Everything is then stored as a graph of people, events and the evidence behind each one.

### Finding gaps and conflicts

- This is where the gaps and conflicts come out. For every planned or scheduled task, we look for evidence from a *different* source about the same time and the same people. The model then judges whether the plan was **confirmed**, **modified** or **contradicted**, quoting both sides. For example: the itinerary says a presentation was on the 16th at 2 PM, but a chat message the next day says the speaker was ill "yesterday" and it never happened.
- No verdict is final until a human accepts or rejects it. The system brings the evidence; the person decides.
- Finally, you can ask questions in plain language ("Did the outing happen on the planned date?"), and every answer comes with links to the exact messages it is based on.
- Cost is kept under control throughout: there is an estimate before anything paid runs, a hard spending limit, and every model response is saved, so nothing is paid for twice.

## Limitations

There are certain limitations we can work on further:

- It works best when there is a plan to check against. A long chain of updates about the same thing (an order delayed twice, a meeting moved three times) is shown on the timeline but not yet tracked as one story with each change linked.
- It is built for one person's or one team's records at a time, not a whole company's systems.

The reasoning behind each design choice, the alternatives considered and what was left out are in [decisions.md](decisions.md).

## Trying it out

### The quickest way: the deployed demo

**Live demo: [evidence-desk-8ji6.onrender.com](https://evidence-desk-8ji6.onrender.com/)** (password shared separately)

Open the link, enter the password, and you will find an entire sample case loaded to view. Start at **Data quality** to see what was wrong with the raw files, then go through **People**, **Timeline** and **Insights**, and finally ask a question on the **Ask** page. You can also upload a file of your own; only the new file gets processed.

The demo runs on a free server that sleeps when no one is using it, so the first visit can take up to a minute to load. After that it responds normally.

### Running it on your own machine

- You need Python 3.12 and `pdftotext`, which comes with poppler (`brew install poppler` on Mac, `apt install poppler-utils` on Linux).
- Get the code and install what it needs:

  ```bash
  git clone https://github.com/a-saldanha/evidence-desk-public.git
  cd evidence-desk-public
  pip install -r requirements.txt
  ```

- **Check that everything works, without any API keys.** These tests use a fake model, so they cost nothing:

  ```bash
  python -m tests.test_temporal
  python -m tests.test_grid_html
  python -m tests.test_resilience
  python -m tests.test_unfamiliar_files
  ```

- **Open the app without spending anything.** This runs the whole interface with a simulated model, and a banner at the top reminds you that the results aren't real:

  ```bash
  EVIDENCE_LLM=scripted streamlit run app/streamlit_app.py
  ```

- **Use it on your real files.** Add your keys to a `.env` file: an Anthropic key, plus a LlamaParse key if you have scanned PDFs or screenshots.

  ```bash
  cp .env.example .env        # then fill in the keys
  set -a; source .env; set +a
  streamlit run app/streamlit_app.py
  ```

  In the app, create a case, enter your name, the nicknames and email addresses you use (so the system knows which person is "you"), and the timezone of your chat exports. Then upload your files and click **Process everything**.

- Before anything paid runs, you see an estimate of the cost. A hard spending limit is set in `.env` (`EVIDENCE_MAX_SPEND_USD`), and every model response is saved, so if something stops halfway you can run it again without paying twice.

### Prefer the command line?

The same pipeline runs without the app:

```bash
python -m reason.run ./my_files --case ./case --self-name "Your Name" \
       --self-alias "Nickname" --self-email you@example.com --chat-tz 5.5
python -m reason.run --case ./case --self-name "Your Name" --ask "Did the meeting on the 16th happen?"
```

`--chat-tz` is your chat's timezone as hours from UTC (5.5 for India).
