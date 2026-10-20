// Exercise Ardour's allocator with libc++ vector growth and map rebinding.
#include "pbd/stack_allocator.h"
#include <cassert>
#include <cstdio>
#include <map>
#include <vector>

int main()
{
    std::vector<int, PBD::StackAllocator<int, 16>> values;
    for (int i = 0; i < 100; ++i) {
        values.push_back(i);
        for (int j = 0; j <= i; ++j) {
            assert(values[j] == j);
        }
    }
    std::map<int, int, std::less<int>,
        PBD::StackAllocator<std::pair<const int, int>, 16>> mapping;
    for (int i = 0; i < 100; ++i) {
        mapping[i] = i * 2;
    }
    for (int i = 0; i < 100; ++i) {
        assert(mapping.at(i) == i * 2);
    }
    auto copied_values = values;
    auto moved_values = std::move(copied_values);
    auto copied_mapping = mapping;
    auto moved_mapping = std::move(copied_mapping);
    assert(moved_values.at(99) == 99 && moved_mapping.at(99) == 198);
    puts("Allocator vector growth and map rebinding passed");
}
