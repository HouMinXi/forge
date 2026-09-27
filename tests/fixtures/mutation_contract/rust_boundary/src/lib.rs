pub fn allows(n: i32) -> bool { n >= 0 }
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn boundary() {
        assert!(allows(1));
        assert!(allows(0));
        assert!(!allows(-1));
    }
}
