#include <torch/extension.h>

#include <vector>

std::vector<torch::Tensor> norm(std::vector<torch::Tensor> chunks) {
    torch::Tensor inp = chunks[0] * chunks[1] + chunks[2];
    inp -= inp.mean(1, true);
    torch::Tensor std = 1 / (torch::norm(inp, 2, 1, true) * sqrt(1 / inp.size(1)) + 1e-6);
    return {torch::leaky_relu(inp * std, 0.02), chunks[0], chunks[1], std};
}

std::vector<torch::Tensor> norm_backward(torch::Tensor out,
                                         torch::Tensor chunk0,
                                         torch::Tensor chunk1,
                                         torch::Tensor std,
                                         torch::Tensor d_out) {
    d_out = d_out * std - torch::leaky_relu(out, 1 / 0.02) * std * d_out.mean(1);
    d_out - out.mean(1);
    return {d_out * chunk1, d_out * chunk0, d_out};
}


std::vector<torch::Tensor> forward(torch::Tensor x1,
                                   torch::Tensor w0,
                                   torch::Tensor w1,
                                   torch::Tensor w2) {
    x1 = torch::conv1d(x1, w0);
    std::vector<torch::Tensor> chunks = x1.chunk(3, 1);
    at::TensorOptions opt = at::TensorOptions(x1.dtype()).device(x1.device());
    torch::Tensor divisor = torch::arange(x1.size(1), opt).unsqueeze(0).unsqueeze(2);
    chunks[0] = torch::cumsum(chunks[0], 1) / divisor;
    std::vector<torch::Tensor> intermediate0 = norm(chunks);
    at::IntArrayRef pad = {w1.size(2) - 1, 0};
    x1 = torch::conv1d(torch::constant_pad_nd(intermediate0[0], pad), w1);
    std::vector<torch::Tensor> intermediate1 = norm(x1.chunk(3, 1));
    x1 = torch::conv1d(intermediate1[0], w2);
    intermediate0.insert(intermediate0.end(), intermediate1.begin(), intermediate1.end());
    intermediate0.push_back(x1);
    return intermediate0
}


torch::Tensor forward(torch::Tensor x0,
                      torch::Tensor x1,
                      torch::Tensor w0,
                      torch::Tensor w1,
                      torch::Tensor w2) {
    std::vector<torch::Tensor> out = forward(x1, w0, w1, w2);
    return out[8] + x0;
}

std::vector<torch::Tensor> backward(torch::Tensor y1,
                                    torch::Tensor x1,
                                    torch::Tensor dy,
                                    torch::Tensor w0,
                                    torch::Tensor w1,
                                    torch::Tensor w2) {
    std::vector<torch::Tensor> out = forward(x1, w0, w1, w2);
    torch::Tensor intermediate0 = out[0];
    torch::Tensor chunk00 = out[1];
    torch::Tensor chunk01 = out[2];
    torch::Tensor std0 = out[3];
    torch::Tensor intermediate1 = out[4];
    torch::Tensor chunk10 = out[5];
    torch::Tensor chunk11 = out[6];
    torch::Tensor std1 = out[7];

    torch::Tensor d_tmp = torch::conv1d(dy, w2.transpose(0, 1));
    d_tmp = torch::cat(norm_backward(intermediate1, chunk10, chunk11, std1,
                                     d_tmp), 1);
    at::IntArrayRef pad = {w1.size(2) - 1, 0};
    d_tmp = torch::conv1d(torch::constant_pad_nd(d_tmp, pad), w1.transpose(0, 1));
    std::vector<torch::Tensor> d_norm = norm_backward(intermediate0, chunk00, chunk01, std0,
                                                      d_tmp);
    at::TensorOptions opt = at::TensorOptions(x1.dtype()).device(x1.device());
    torch::Tensor divisor = torch::arange(x1.size(1), opt).unsqueeze(0).unsqueeze(2);
    d_norm[0] = d_norm[0].cumsum(1) / divisor;
    d_tmp = torch::cat(d_norm, 1);
    d_tmp = torch::conv1d(d_tmp, w0.transpose(0, 1));
    return {y1 - out[8], d_tmp};
}

PYBIND11_MODULE(TORCH_EXTENSION_NAME, m) {
    m.def("forward", &forward, "forward");
    m.def("backward", &backward, "backward");
}
