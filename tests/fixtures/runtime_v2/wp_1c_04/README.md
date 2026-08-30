# WP-1C-04 lifecycle fixture

`lifecycle-golden.json` is the shared Rust/Python contract for the bundled Core lifecycle. It
freezes only the three target layouts, lifecycle order, protocol capabilities, and existing
deadlines. It contains no Assistant, chat, Router, Operation, resource, or user-data fields.
