// The host and Apple SDK checks compile the production module directly.
// No replacement queue, native AudioUnit, or echo canceller is supplied here.
#[path = "../src/client/ios_duplex.rs"]
mod ios_duplex;
