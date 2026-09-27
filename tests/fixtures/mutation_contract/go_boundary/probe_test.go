package probe

import "testing"

func TestBoundary(t *testing.T) {
    if Allows(-1) || !Allows(1) {
        t.Fatal("sides")
    }
    if !Allows(0) {
        t.Fatal("boundary")
    }
}
