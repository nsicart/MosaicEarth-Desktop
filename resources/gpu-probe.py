"""Bounded, data-free CUDA driver/kernel check inside the actual Docker runtime."""
import ctypes as c
import json

PTX = b'''.version 6.0
.target sm_60
.address_size 64
.visible .entry probe(.param .u64 output) {
 .reg .b64 %rd;
 .reg .b32 %r;
 ld.param.u64 %rd, [output];
 mov.u32 %r, 42;
 st.global.u32 [%rd], %r;
 ret;
}
'''


def detect():
    cuda = c.CDLL('libcuda.so.1')
    def call(name, *args):
        code = getattr(cuda, name)(*args)
        if code:
            raise RuntimeError('CUDA check failed: ' + name + ' (' + str(code) + ')')
    call('cuInit', 0)
    count, driver = c.c_int(), c.c_int()
    call('cuDeviceGetCount', c.byref(count))
    call('cuDriverGetVersion', c.byref(driver))
    devices, errors = [], []
    for index in range(min(count.value, 16)):
        context, memory, module = c.c_void_p(), c.c_uint64(), c.c_void_p()
        try:
            device, major, minor = c.c_int(), c.c_int(), c.c_int()
            total, name = c.c_size_t(), c.create_string_buffer(256)
            call('cuDeviceGet', c.byref(device), index)
            call('cuDeviceGetName', name, len(name), device)
            call('cuDeviceComputeCapability', c.byref(major), c.byref(minor), device)
            call('cuDeviceTotalMem_v2', c.byref(total), device)
            capability = major.value * 10 + minor.value
            if not 60 <= capability <= 89 or driver.value < 11080:
                continue
            call('cuCtxCreate_v2', c.byref(context), 0, device)
            call('cuMemAlloc_v2', c.byref(memory), c.c_size_t(4))
            call('cuModuleLoadData', c.byref(module), c.c_char_p(PTX))
            function = c.c_void_p()
            call('cuModuleGetFunction', c.byref(function), module, b'probe')
            arguments = (c.c_void_p * 1)(c.cast(c.byref(memory), c.c_void_p))
            call('cuLaunchKernel', function, 1, 1, 1, 1, 1, 1, 0, c.c_void_p(), arguments, c.c_void_p())
            result = c.c_int()
            call('cuMemcpyDtoH_v2', c.byref(result), memory, c.c_size_t(4))
            if result.value != 42:
                raise RuntimeError('CUDA kernel returned an invalid result')
            devices.append({'index': index, 'name': name.value.decode(errors='replace'),
                            'capability': capability, 'memory_mib': total.value // 1048576})
        except (OSError, RuntimeError) as exc:
            errors.append(str(exc))
            continue
        finally:
            if memory.value:
                cuda.cuMemFree_v2(memory)
            if module.value:
                cuda.cuModuleUnload(module)
            if context.value:
                cuda.cuCtxDestroy_v2(context)
    return {'driver_cuda': driver.value, 'devices': devices, 'errors': errors}


if __name__ == '__main__':
    try:
        print(json.dumps(detect()))
    except (OSError, RuntimeError) as exc:
        print(json.dumps({'devices': [], 'reason': str(exc)}))
