//! Cross-platform service boundaries for Sakura Runtime v2.
//!
//! WP-1P-01 freezes contracts only. Concrete backends and production wiring
//! are intentionally introduced by WP-1P-02 through WP-1P-05.

mod contracts;
mod error;
mod native_diagnostics;
mod process_tree_backend;
mod runtime_locator;
mod target;
mod window_backend;

// These re-exports are the stable facade consumed once concrete backends are
// wired. The binary does not call them during the WP-1P-01 contract-only step.
#[allow(unused_imports)]
pub use contracts::*;
#[allow(unused_imports)]
pub use error::*;
#[allow(unused_imports)]
pub use native_diagnostics::*;
#[allow(unused_imports)]
pub use process_tree_backend::*;
#[allow(unused_imports)]
pub use runtime_locator::*;
#[allow(unused_imports)]
pub use target::*;
#[allow(unused_imports)]
pub use window_backend::*;
