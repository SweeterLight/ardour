"""Windows target detection shared by native and cross Waf builds."""

def configure(conf):
    requested = conf.options.windows_arch
    if conf.options.dist_target not in ('mingw', 'msvc'):
        if requested != 'auto':
            conf.fatal('--windows-arch requires --dist-target=mingw or msvc')
        return

    targets = (
        ('arm64', 'defined(__aarch64__) || defined(_M_ARM64)'),
        ('x64', 'defined(__x86_64__) || defined(_M_X64)'),
        ('x86', 'defined(__i386__) || defined(_M_IX86)'),
    )
    for arch, condition in targets:
        if conf.check_cc(
                fragment='#if !defined(_WIN32) || !(%s)\n#error Wrong target\n#endif\nint main(void) { return 0; }\n' % condition,
                mandatory=False, execute=False,
                msg='Checking for Windows %s compiler target' % arch):
            if requested not in ('auto', arch):
                conf.fatal('--windows-arch=%s does not match compiler target %s' % (requested, arch))
            conf.env['WINDOWS_ARCH'] = arch
            return
    conf.fatal('Cannot identify Windows compiler target (expected x86, x64, or arm64)')
