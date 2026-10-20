#include "mutex_test.h"
#include <cppunit/TestAssert.h>
#include <thread>

CPPUNIT_TEST_SUITE_REGISTRATION (MutexTest);

using namespace std;

MutexTest::MutexTest ()
{
}

void
MutexTest::test_mutex ()
{
	PBD::Mutex::Lock lm (_mutex);

	CPPUNIT_ASSERT (lm.locked( ));

	/* This will fail on POSIX systems but not on some older versions of glib
	 * on win32 as TryEnterCriticalSection is used and it will return true
	 * as CriticalSection is reentrant and fail the assertion.
	 */
	CPPUNIT_ASSERT (!_mutex.trylock());
}

void
MutexTest::test_cond ()
{
	PBD::Mutex::Lock lm (_mutex);

	/* Condition variables may wake spuriously, including on Windows. */
	const auto deadline = std::chrono::steady_clock::now () + std::chrono::seconds (1);
	while (std::chrono::steady_clock::now () < deadline) {
		const auto remaining = std::chrono::duration_cast<std::chrono::milliseconds> (
			deadline - std::chrono::steady_clock::now ()) + std::chrono::milliseconds (1);
		if (!_cond.wait_for (_mutex, remaining)) {
			CPPUNIT_ASSERT (std::chrono::steady_clock::now () >= deadline);
			break;
		}
	}

	bool notified = false;
	thread t1 ([&]() { PBD::Mutex::Lock lock (_mutex); notified = true; _cond.signal (); });
	while (!notified) { _cond.wait (_mutex); }
	CPPUNIT_ASSERT (lm.locked ()); // check that mutex is locked
	CPPUNIT_ASSERT (!_mutex.trylock ()); // check that mutex is locked
	t1.join();

	notified = false;
	thread t2 ([&]() { PBD::Mutex::Lock lock (_mutex); notified = true; _cond.signal (); });
	const auto notify_deadline = std::chrono::steady_clock::now () + std::chrono::seconds (1);
	while (!notified && std::chrono::steady_clock::now () < notify_deadline) {
		_cond.wait_for (_mutex, std::chrono::milliseconds (10));
	}
	const bool received = notified;
	lm.release ();
	t2.join ();
	CPPUNIT_ASSERT_MESSAGE ("condition notification was not received", received);
}
