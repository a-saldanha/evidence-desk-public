# Decisions

## Why I built this

I built the project to help solve a problem I faced myself. The entire timeline from paper acceptance to travelling to the conference involved a lot of cross-communication between multiple people, across different mediums such as emails, WhatsApp, calls, etc.

This was very manageable in the beginning, but soon I had to keep track of 3 different email addresses, keep adding people and communicating via phone calls, Google Meet and website updates, and the entire process became hard to navigate and keep track of.

I approached this manually by keeping track of the latest people I needed to coordinate with, the help I needed, and whom I could approach.

However, once I came back from the conference, I couldn't recall a lot of critical details about it, such as whom I received money from, what the exact expenses were, and what happened to the different plans I had made during the conference.

In a similar manner, after talking to multiple people about real challenges they face with multi-medium data, a general theme was sorting out which sources are the latest and most reliable. I've explained this a bit more in the repository's README.md file.

In the limited time I had, I tried building certain features and testing the best way to arrive at a solution.

## Architecture

- In the first version of this project, an LLM was handling everything end to end.
- That is, after preprocessing the input data, all the information was simply dumped into the LLM to generate the relationships between entities, the graphs, the conflicts, etc. However, this led to outputs that were not standardised. LLMs also hallucinate, and are unpredictable and hard to steer when a lot of information is dumped in one go.
- So in version 2, I broke the work down into layers. Parsing, de-duplication and cleaning happen in deterministic layers using scripts. The LLM reads the cleaned text and extracts events, people, places and what the text says about time, and code then turns that into actual dates. People who share an email address or phone number are merged automatically; all other possible matches are left for the user to resolve. The LLM can only perform 3 tasks: extracting information, judging plans against evidence, and answering questions asked by the end user.
- So we move away from a generic "summarise everything" prompt to very focused extraction, which then gets checked by the deterministic layers.
- An improvement was seen in date-related tasks, where words like "yesterday" and "tomorrow" in chats were misunderstood by the LLM. By using Python scripts, we connect these words with the actual timestamps of the chat messages to resolve the temporal information.

## Features where an LLM was not necessary

- For the same input, the deterministic steps (parsing, de-duplication, dates) always give the same output, and every LLM response is saved, so re-running on the same data gives identical results. This matters since different people may be running it at different stages of the event.
- We add these layers so that information moves in a very predictable format, which allows for easy modification, enhancement and error correction in the future if needed.
- Dates are computed by code to preserve temporal details.
- Every quote that comes back from the LLM is searched for again in the raw text, to check that the source actually says it. If it can't be found, the quote is dropped.
- Itinerary tables (Excel sheets, or screenshots converted into tables) are read row by row by code and not sent to the model, as that has a high chance of failure and misrepresentation.
- Files are referenced by hash values of their content and not simply by their names, since it is possible to have the same name for multiple files.
- We also remove duplicated information in preprocessing, so that we can maintain the quality of information down the pipeline.
- In the case of multiple identities, people who share an email address or phone number are merged automatically, and the rest is left to the user. A score is provided, which makes it easier to skim through, resolve the lower-scored ones and prioritise. The reason is to ensure that only accurate information is left, which only a user may be able to provide; no amount of code may be able to give a 100% accurate result.

## Software and libraries

- Since this is software I made for a quick demo, and something that will be used by a single person at a time for a single event, it made sense to choose a stack that was relatively easy to work with and lightweight enough to run on device.
- So I went with Streamlit and an SQLite database to keep it simple and efficient.
- LlamaParse with cost-tier routing.
- Small, auditable tools over heavy infrastructure. Text matching uses rapidfuzz instead of embeddings, Q&A retrieval uses a hand-written BM25 instead of a vector database, and file types are detected by a small hand-written sniffer.
- The LLM is behind one swappable interface. All model calls go through `reason/llm.py`, so another provider can be plugged in.

## Data quality

- Preprocessing involves handling the different kinds of email formats from different services.
- We then separate out information that is not important, such as headers, quotes, forwards, etc., before it reaches the LLM layer.
- We consider problems such as date formatting: DD/MM/YY or MM/DD/YY.
- Multiple possible issues, including missing attachments, are all pointed out to the user on a separate page.

## Decision making

- This is a human-in-the-loop product, where a human is necessary to review and then accept or reject the conflicts present in the data, and to validate the missing information within the data.
- Since it is not possible to completely define all existing information, it only makes sense to involve a human in the loop, making the entire process more reliable.

## UX

- Sensitive information is hidden.
- Citations show the source documents the reference was taken from.
- Snapshots can be saved and loaded.
- Cost estimates and the total amount spent via the API are shown to the user.
- Responses are stored on disk, so that in case of a crash there is no need to spend the money again on generating the same answers.

## Known gaps

- We use fuzzy matching when we verify LLM outputs against the source. A quote with a changed number (for example "16 Sep" instead of "18 Sep") can still match, since one changed digit barely affects the similarity score.
- Insights may get duplicated: two scheduled events can each be judged as evidence for the other, so the same relationship shows up twice.
- The offline tests use a simulated model and need no API keys, but the end-to-end tests need access to real-world documents.
