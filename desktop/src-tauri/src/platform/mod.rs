//! Cross-platform service boundaries for Sakura Runtime v2.
//!
//! WP-1P-01 freezes contracts only. Concrete backends and production wiring
//! are intentionally introduced by WP-1P-02 through WP-1P-05.

mod contracts;
mod error;
mod target;

// These re-exports are the stable facade consumed once concrete backends are
// wired. The binary does not call them during the WP-1P-01 contract-only step.
#[allow(unused_imports)]
pub use contracts::*;
#[allow(unused_imports)]
pub use error::*;
#[allow(unused_imports)]
pub use target::*;
