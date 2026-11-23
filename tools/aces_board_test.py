#!/usr/bin/env python3
"""ONE HUMAN IS SEVERAL PARTICIPANTS.

A Participant row is one golfer in one round, so somebody who plays
three times is three rows. An all-time board counting ROWS would put
the same person on the wall three times with one ace each, instead of
once with three -- which is the opposite of what a wall is for.

`person_key` is the handle that survives across rounds. It is pure, so
it is read off the shipping source and exec'd without sqlalchemy.

    python3 tools/aces_board_test.py
"""

import pathlib

_SRC = (pathlib.Path(__file__).resolve().parents[1]
        / "backend/app/services/aces.py").read_text()
_ns: dict = {}
exec(
    _SRC[_SRC.index("def person_key("):_SRC.index("def all_time_board(")],
    _ns,
)
person_key = _ns["person_key"]


def main() -> int:
    fails = []

    def same(a, b, why):
        if person_key(*a) != person_key(*b):
            fails.append(f"should be one person — {why}")

    def apart(a, b, why):
        if person_key(*a) == person_key(*b):
            fails.append(f"should be two people — {why}")

    # (user_id, email, name)
    print("one person across rounds:")

    # An account is the only identifier anybody deliberately keeps.
    same((7, "ben@x.com", "Ben Baldwin"),
         (7, "ben.baldwin@work.com", "B Baldwin"),
         "same account, different email and spelling")
    print("   same account, different email+name -> one person")

    # No account: the email, however it was typed.
    same((None, "Ben@X.com", "Ben Baldwin"),
         (None, " ben@x.com ", "Ben"),
         "same email, different case and whitespace")
    print("   same email, different case/spacing  -> one person")

    # No account and no email: the name is all there is.
    same((None, None, "Ben Baldwin"), (None, "", " ben baldwin "),
         "same name, one round with a blank email")
    print("   same name, blank email              -> one person")

    print("\ndifferent people:")
    apart((1, "a@x.com", "Ben"), (2, "a@x.com", "Ben"),
          "two accounts")
    apart((None, "a@x.com", "Ben"), (None, "b@x.com", "Ben"),
          "same name, different emails")
    apart((None, None, "Ben"), (None, None, "Alex"), "different names")
    print("   two accounts / two emails / two names -> kept apart")

    # AN EMAIL AND A NAME MUST NEVER COLLIDE. Without the prefix a
    # golfer called "ben@x.com" would merge with the address.
    apart((None, "ben@x.com", "whoever"), (None, None, "ben@x.com"),
          "an address used as a name")
    print("   an address typed as a name            -> kept apart")

    # An account outranks a matching email, so the same human signing
    # in partway through their history does split. Recorded because it
    # is a real limitation, not an oversight: merging on email would
    # let anyone claim another golfer's aces by typing their address.
    if person_key(5, "a@x.com", "Ben") == person_key(None, "a@x.com", "Ben"):
        fails.append("an account and a guest round merged on email")
    print("\n   signed-in and guest rounds DO split (deliberate: merging")
    print("   on email would let anyone claim another golfer's aces)")

    if fails:
        print("\nFAILED:")
        for f in fails:
            print("   " + f)
        return 1
    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
