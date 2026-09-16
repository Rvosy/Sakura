//! Cross-platform service boundaries for Sakura Runtime v2.
//!
//! Native backends share these contracts and are selected at compile time.

mod contracts;
mod error;
mod native_diagnostics;
mod process_tree_backend;
mod runtime_locator;
mod target;
mod window_backend;

// Each consumer imports the concrete services or contracts it needs.
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
