/*
 * Copyright (C) 2026 Robin Gareus <robin@gareus.org>
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, write to the Free Software Foundation, Inc.,
 * 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
 */

#pragma once

#include <shared_mutex>
#include <condition_variable>
#include <mutex>

#include "pbd/libpbd_visibility.h"

namespace PBD
{

class LIBPBD_API RWLock
{
public:
	enum LockFlags {
		Lock,
		NotLock,
		TryLock
	};

	class ReaderLock;
	class WriterLock;

	RWLock ();
	~RWLock ();

	inline void reader_lock ()
	{
		_rw_lock.lock_shared ();
	}
	inline bool reader_trylock ()
	{
		return _rw_lock.try_lock_shared ();
	}
	inline void reader_unlock ()
	{
		_rw_lock.unlock_shared ();
	}

	inline void writer_lock ()
	{
		_rw_lock.lock ();
	}
	inline bool writer_trylock ()
	{
		return _rw_lock.try_lock ();
	}
	inline void writer_unlock ()
	{
		_rw_lock.unlock ();
	}

private:
	RWLock (const RWLock&)            = delete;
	RWLock& operator= (const RWLock&) = delete;

#if defined(PLATFORM_WINDOWS) && defined(_LIBCPP_VERSION)
	/* Ardour permits additional read locks while a writer waits (including
	 * nested reads). libc++'s Windows shared_mutex blocks those readers,
	 * deadlocking that sequence. Preserve the reader-priority contract.
	 */
	class ReaderPreferredLock {
	public:
		void lock_shared () {
			std::unique_lock<std::mutex> lock (_mutex);
			_changed.wait (lock, [this] { return !_writer; });
			++_readers;
		}
		bool try_lock_shared () {
			std::unique_lock<std::mutex> lock (_mutex, std::try_to_lock);
			if (!lock.owns_lock () || _writer) { return false; }
			++_readers;
			return true;
		}
		void unlock_shared () {
			std::lock_guard<std::mutex> lock (_mutex);
			if (--_readers == 0) { _changed.notify_all (); }
		}
		void lock () {
			std::unique_lock<std::mutex> lock (_mutex);
			_changed.wait (lock, [this] { return !_writer && _readers == 0; });
			_writer = true;
		}
		bool try_lock () {
			std::unique_lock<std::mutex> lock (_mutex, std::try_to_lock);
			if (!lock.owns_lock () || _writer || _readers) { return false; }
			_writer = true;
			return true;
		}
		void unlock () {
			std::lock_guard<std::mutex> lock (_mutex);
			_writer = false;
			_changed.notify_all ();
		}
	private:
		std::mutex _mutex;
		std::condition_variable _changed;
		unsigned _readers = 0;
		bool _writer = false;
	};
	ReaderPreferredLock _rw_lock;
#else
	std::shared_mutex _rw_lock;
#endif
};

class LIBPBD_API RWLock::ReaderLock
{
public:
	ReaderLock (RWLock& rwlock, RWLock::LockFlags m = Lock);
	~ReaderLock ();

	inline void acquire ()
	{
		_rwlock.reader_lock ();
		_locked = true;
	}

	inline bool try_acquire ()
	{
		_locked = _rwlock.reader_trylock ();
		return _locked;
	};

	inline void release ()
	{
		_rwlock.reader_unlock ();
		_locked = false;
	}

	inline bool locked () const
	{
		return _locked;
	}

private:
	RWLock& _rwlock;
	bool    _locked;

	ReaderLock (RWLock::ReaderLock const&)                    = delete;
	RWLock::ReaderLock& operator= (RWLock::ReaderLock const&) = delete;
};

class LIBPBD_API RWLock::WriterLock
{
public:
	WriterLock (RWLock& rwlock, RWLock::LockFlags m = Lock);
	~WriterLock ();

	inline void acquire ()
	{
		_rwlock.writer_lock ();
		_locked = true;
	}

	inline bool try_acquire ()
	{
		_locked = _rwlock.writer_trylock ();
		return _locked;
	}

	inline void release ()
	{
		_rwlock.writer_unlock ();
		_locked = false;
	}

	inline bool locked () const
	{
		return _locked;
	}

private:
	RWLock& _rwlock;
	bool    _locked;

	WriterLock (RWLock::WriterLock const&)                    = delete;
	RWLock::WriterLock& operator= (RWLock::WriterLock const&) = delete;
};

} // namespace PBD
