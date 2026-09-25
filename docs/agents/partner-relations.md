# Partner Relations Department

Partner Relations is a first-class Alice Pro Department backed by owner-scoped
partner, contact, message and follow-up records.

## Ownership and authorization

All records carry the trusted owner id resolved from the Alice authentication
context. Tool execution prefers the user id already attached to UniversalToolCall
and falls back to the trusted request identity. Client-supplied owner ids are
not accepted as authority.

## Local tools

- partner.create / partner.get / partner.update / partner.list;
- partner.contact.create / partner.contact.list / partner.contact.update / partner.contact.delete;
- partner.message.prepare / partner.message.send;
- partner.thread.get;
- partner.status.update;
- partner.followup.create / partner.followup.complete.

Mutating tools use the common Universal Tool Executor and require approval.
Read-only list/get/thread tools do not require approval.

## Communication boundary

partner.message.prepare stores an outgoing message as prepared history.
partner.message.send is the approval-gated action that moves the prepared
message to queued state. The current repository has no configured external
transport adapter, so the tool explicitly reports transport=not_configured
rather than claiming that an email or messenger message was delivered.

Reference fields reference_type/reference_id are stored on messages and
follow-ups so future order/result workflows can correlate commercial work.

## ExecutionTrace

Partner tool actions emit trace events containing action type, partner/message/
contact/follow-up identifiers, trusted owner id and outcome metadata. Message
body, credentials and other secret material are not copied into trace events.

## HTTP/UI

The Departments view registers a Partner Relations department. Selecting it
opens a mobile-friendly owner-scoped workspace for partner records, statuses,
contacts, communication history and follow-ups.

External message delivery remains a separate integration concern. Adding a
transport adapter must preserve the existing approval and governance boundary.
